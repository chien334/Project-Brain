#!/usr/bin/env python3
"""Local offline & Local AI OCR MCP extension (PaddleOCR, RapidOCR, Tesseract, Ollama/vLLM/LM Studio/llama.cpp).

Exposes tools to report compute/OCR/Local-AI capabilities and OCR images or PDFs
without any cloud API key. Heavy dependencies (paddleocr, rapidocr_onnxruntime,
pytesseract, pymupdf, torch) are imported lazily inside each tool so the extension
always loads cleanly.
"""

import asyncio
import importlib.util
import io
import os
import platform
from pathlib import Path
from typing import Annotated, Any, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

mcp = FastMCP("ocr_local_mcp")

_LOCAL_AI_DEFAULTS = {
    "ollama": ("OLLAMA_BASE_URL", "http://localhost:11434", "/api/tags"),
    "vllm": ("VLLM_BASE_URL", "http://localhost:8000/v1", "/models"),
    "lmstudio": ("LMSTUDIO_BASE_URL", "http://localhost:1234/v1", "/models"),
    "llamacpp": ("LLAMACPP_BASE_URL", "http://localhost:8080/v1", "/models"),
}
_RAPIDOCR_CACHE: dict[str, Any] = {}


def _pkg_installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _detect_compute_devices_inline() -> dict[str, Any]:
    """Inline compute device & runtime capability detection (fallback if ocr_runtime unavailable)."""
    cuda_available = False
    cuda_count = 0
    cuda_names: list[str] = []
    mps_available = False
    onnx_providers: list[str] = []

    try:
        import paddle

        if paddle.device.is_compiled_with_cuda():
            cuda_available = True
            try:
                cuda_count = int(paddle.device.cuda.device_count())
            except Exception:
                cuda_count = 1
    except Exception:
        pass

    try:
        import torch

        if torch.cuda.is_available():
            cuda_available = True
            cuda_count = max(cuda_count, int(torch.cuda.device_count()))
            for idx in range(torch.cuda.device_count()):
                try:
                    cuda_names.append(torch.cuda.get_device_name(idx))
                except Exception:
                    cuda_names.append(f"cuda:{idx}")
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            mps_available = True
    except Exception:
        pass

    try:
        import onnxruntime as ort

        onnx_providers = list(ort.get_available_providers())
        if "CUDAExecutionProvider" in onnx_providers:
            cuda_available = True
    except Exception:
        pass

    if cuda_available and cuda_count > 0:
        recommended = "cuda:0"
    elif mps_available:
        recommended = "mps"
    else:
        recommended = "cpu"

    return {
        "cuda_available": cuda_available,
        "cuda_device_count": cuda_count,
        "cuda_devices": cuda_names,
        "mps_available": mps_available,
        "onnx_providers": onnx_providers,
        "cpu_count": os.cpu_count() or 1,
        "platform": platform.system().lower(),
        "machine": platform.machine().lower(),
        "recommended_device": recommended,
    }


async def _probe_local_ai_server(name: str, env_var: str, default_url: str, probe_path: str) -> dict[str, Any]:
    """Probe a local AI server endpoint with a short timeout."""
    import httpx

    base = (os.getenv(env_var) or os.getenv("LOCAL_AI_URL") or default_url).strip().rstrip("/")
    if name == "ollama" and base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    url = f"{base}{probe_path}"
    try:
        async with httpx.AsyncClient(timeout=0.8) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            models: list[str] = []
            if name == "ollama" and isinstance(data, dict):
                models = [str(m.get("name")) for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]
            elif isinstance(data, dict) and isinstance(data.get("data"), list):
                models = [str(m.get("id")) for m in data["data"] if isinstance(m, dict) and m.get("id")]
            return {"reachable": True, "url": base, "models": models}
        return {"reachable": False, "url": base, "status_code": resp.status_code, "models": []}
    except Exception:
        return {"reachable": False, "url": base, "models": []}


async def _probe_all_local_ai() -> dict[str, dict[str, Any]]:
    tasks = [
        _probe_local_ai_server(name, env_var, default_url, probe_path)
        for name, (env_var, default_url, probe_path) in _LOCAL_AI_DEFAULTS.items()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: dict[str, dict[str, Any]] = {}
    for name, res in zip(_LOCAL_AI_DEFAULTS.keys(), results):
        if isinstance(res, dict):
            out[name] = res
        else:
            out[name] = {"reachable": False, "models": []}
    return out


def _rapidocr_blocking(image_bytes: bytes, device: str = "auto") -> str:
    """Run RapidOCR (ONNX Runtime) synchronously on image bytes."""
    import numpy as np
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR

    dev = (device or "auto").strip().lower()
    use_cuda = dev in ("gpu", "cuda") or dev.startswith("cuda:")
    if dev == "auto":
        try:
            import onnxruntime as ort

            use_cuda = "CUDAExecutionProvider" in ort.get_available_providers()
        except Exception:
            use_cuda = False

    cache_key = "cuda" if use_cuda else "cpu"
    engine = _RAPIDOCR_CACHE.get(cache_key)
    if engine is None:
        try:
            engine = RapidOCR(det_use_cuda=use_cuda, cls_use_cuda=use_cuda, rec_use_cuda=use_cuda)
        except TypeError:
            engine = RapidOCR()
        _RAPIDOCR_CACHE[cache_key] = engine

    with Image.open(io.BytesIO(image_bytes)) as im:
        arr = np.asarray(im.convert("RGB"))[:, :, ::-1].copy()

    result, _ = engine(arr)
    if not result:
        return ""
    lines = [str(item[1]).strip() for item in result if item and len(item) >= 2 and item[1]]
    return "\n".join(lines).strip()


def _tesseract_blocking(image_bytes: bytes) -> str:
    """Run Tesseract OCR synchronously on image bytes."""
    import pytesseract
    from PIL import Image

    lang = os.getenv("OCR_TESSERACT_LANG", "eng").strip() or "eng"
    with Image.open(io.BytesIO(image_bytes)) as im:
        return (pytesseract.image_to_string(im, lang=lang) or "").strip()


async def _try_ocr_runtime(
    image_bytes: bytes,
    engine: str,
    device: str,
    local_ai_model: Optional[str],
    local_ai_url: Optional[str],
    cpu_threads: Optional[int],
) -> Optional[str]:
    """Try delegating to UnifiedOCREngine / LocalVisionParser when ocr_runtime.py is available."""
    if cpu_threads is not None:
        return None
    try:
        from projectbrain.ocr_runtime import LocalVisionParser, UnifiedOCREngine  # type: ignore
    except Exception:
        return None

    if engine in ("ollama", "vllm", "lmstudio", "llamacpp"):
        try:
            parser = LocalVisionParser(
                provider=engine,
                model=local_ai_model,
                base_url=local_ai_url,
            )
            res = await parser.parse_image(
                image_bytes=image_bytes,
                mime_type="image/png",
                model=local_ai_model,
            )
            if isinstance(res, str) and res.strip():
                return res.strip()
        except Exception:
            return None

    if engine in ("paddle", "rapidocr", "tesseract", "hybrid_local"):
        try:
            unified = UnifiedOCREngine()
            res = await unified.extract_image(
                image_bytes=image_bytes,
                mime_type="image/png",
                engine=engine,
                device=device,
                local_ai_model=local_ai_model,
                local_ai_url=local_ai_url,
            )
            if isinstance(res, dict):
                txt = str(res.get("text") or res.get("markdown") or "").strip()
                if txt:
                    return txt
        except Exception:
            return None

    return None


async def _run_ocr_on_bytes(
    image_bytes: bytes,
    device: str = "auto",
    engine: str = "auto",
    local_ai_model: Optional[str] = None,
    local_ai_url: Optional[str] = None,
    cpu_threads: Optional[int] = None,
) -> tuple[str, str]:
    """Run OCR on image bytes using the chosen engine/device. Returns (text, engine_used)."""
    eng = (engine or "auto").strip().lower()
    dev = (device or "auto").strip().lower()

    runtime_txt = await _try_ocr_runtime(
        image_bytes, eng, dev, local_ai_model, local_ai_url, cpu_threads
    )
    if runtime_txt:
        return runtime_txt, eng

    from extensions_mcp.image_to_markdown.paddle_client import (
        extract_text_local,
        is_available as paddle_ok,
    )

    if eng == "paddle":
        if not paddle_ok():
            raise RuntimeError(
                "PaddleOCR is not installed. Run: pip install -e '.[ocr-cpu]' or '.[ocr-gpu]'"
            )
        txt = await extract_text_local(
            image_bytes, "image/png", device=dev, cpu_threads=cpu_threads
        )
        return txt or "", "paddle"

    if eng == "rapidocr":
        if not _pkg_installed("rapidocr_onnxruntime"):
            raise RuntimeError(
                "RapidOCR is not installed. Run: pip install -e '.[ocr-onnx]' or '.[ocr-onnx-gpu]'"
            )
        txt = await asyncio.to_thread(_rapidocr_blocking, image_bytes, dev)
        return txt or "", "rapidocr"

    if eng == "tesseract":
        if not _pkg_installed("pytesseract"):
            raise RuntimeError("pytesseract is not installed. Run: pip install pytesseract")
        txt = await asyncio.to_thread(_tesseract_blocking, image_bytes)
        return txt or "", "tesseract"

    if eng in _LOCAL_AI_DEFAULTS:
        from extensions_mcp.image_to_markdown.vision_client import (
            DEFAULT_PROMPT,
            extract_text_from_image,
        )

        eff_model = local_ai_model or os.getenv("LOCAL_AI_MODEL") or (
            "qwen2.5vl" if eng == "ollama" else "default"
        )
        txt = await extract_text_from_image(
            image_bytes,
            "image/png",
            DEFAULT_PROMPT,
            eff_model,
            provider=eng,
            base_url=local_ai_url,
        )
        return txt or "", eng

    if eng == "hybrid_local":
        draft = ""
        used = "none"
        if paddle_ok():
            draft = await extract_text_local(
                image_bytes, "image/png", device=dev, cpu_threads=cpu_threads
            )
            used = "paddle"
        elif _pkg_installed("rapidocr_onnxruntime"):
            draft = await asyncio.to_thread(_rapidocr_blocking, image_bytes, dev)
            used = "rapidocr"
        elif _pkg_installed("pytesseract"):
            draft = await asyncio.to_thread(_tesseract_blocking, image_bytes)
            used = "tesseract"

        servers = await _probe_all_local_ai()
        reachable_provider = next(
            (name for name, info in servers.items() if info.get("reachable")), None
        )
        if reachable_provider or local_ai_url or local_ai_model:
            target_provider = reachable_provider or "ollama"
            from extensions_mcp.image_to_markdown.vision_client import (
                DEFAULT_PROMPT,
                extract_text_from_image,
            )

            models = (servers.get(target_provider) or {}).get("models") or []
            eff_model = local_ai_model or (models[0] if models else None) or (
                "qwen2.5vl" if target_provider == "ollama" else "default"
            )
            prompt = DEFAULT_PROMPT
            if draft.strip():
                prompt = (
                    f"{DEFAULT_PROMPT}\n\n"
                    f"Reference raw OCR draft (correct errors & preserve tables/headings):\n{draft.strip()}"
                )
            try:
                ai_txt = await extract_text_from_image(
                    image_bytes,
                    "image/png",
                    prompt,
                    eff_model,
                    provider=target_provider,
                    base_url=local_ai_url,
                )
                if ai_txt and ai_txt.strip():
                    return ai_txt.strip(), f"hybrid_local:{used}+{target_provider}"
            except Exception:
                if draft.strip():
                    return draft.strip(), used
                raise

        if draft.strip():
            return draft.strip(), used
        if used == "none":
            raise RuntimeError(
                "No local OCR engine or Local AI server available. "
                "Install paddleocr / rapidocr-onnxruntime / pytesseract or start Ollama/vLLM/LM Studio."
            )
        return "", used

    # eng == "auto": paddle -> rapidocr -> tesseract -> reachable local AI
    any_engine_present = False
    if paddle_ok():
        any_engine_present = True
        txt = await extract_text_local(
            image_bytes, "image/png", device=dev, cpu_threads=cpu_threads
        )
        if txt and txt.strip():
            return txt.strip(), "paddle"

    if _pkg_installed("rapidocr_onnxruntime"):
        any_engine_present = True
        try:
            txt = await asyncio.to_thread(_rapidocr_blocking, image_bytes, dev)
            if txt and txt.strip():
                return txt.strip(), "rapidocr"
        except Exception:
            pass

    if _pkg_installed("pytesseract"):
        any_engine_present = True
        try:
            txt = await asyncio.to_thread(_tesseract_blocking, image_bytes)
            if txt and txt.strip():
                return txt.strip(), "tesseract"
        except Exception:
            pass

    servers = await _probe_all_local_ai()
    reachable_provider = next(
        (name for name, info in servers.items() if info.get("reachable")), None
    )
    if reachable_provider or local_ai_url:
        any_engine_present = True
        target_provider = reachable_provider or "ollama"
        from extensions_mcp.image_to_markdown.vision_client import (
            DEFAULT_PROMPT,
            extract_text_from_image,
        )

        models = (servers.get(target_provider) or {}).get("models") or []
        eff_model = local_ai_model or (models[0] if models else None) or (
            "qwen2.5vl" if target_provider == "ollama" else "default"
        )
        txt = await extract_text_from_image(
            image_bytes,
            "image/png",
            DEFAULT_PROMPT,
            eff_model,
            provider=target_provider,
            base_url=local_ai_url,
        )
        return (txt or "").strip(), target_provider

    if not any_engine_present:
        raise RuntimeError(
            "No local OCR engine installed. Run: pip install -e '.[ocr-cpu]' or '.[ocr-onnx]'"
        )
    return "", "paddle" if paddle_ok() else "auto"


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
    """Report complete local OCR & Local AI status: GPU/CUDA/MPS/ONNX/CPU, engines, and reachable Local AI servers."""
    from extensions_mcp.image_to_markdown.config import CONFIG
    from extensions_mcp.image_to_markdown.paddle_client import _should_enable_mkldnn

    compute_devices: dict[str, Any] = {}
    try:
        from projectbrain.ocr_runtime import ComputeDeviceManager  # type: ignore

        if hasattr(ComputeDeviceManager, "detect_hardware"):
            hw = ComputeDeviceManager.detect_hardware()
            if isinstance(hw, dict):
                compute_devices = dict(hw)
                if "recommended_device" not in compute_devices:
                    resolved = ComputeDeviceManager.resolve_device(
                        requested=CONFIG.ocr_device, engine=CONFIG.ocr_engine, hardware=hw
                    )
                    compute_devices["recommended_device"] = resolved.get("device_str", "cpu")
    except Exception:
        pass
    if not compute_devices:
        compute_devices = _detect_compute_devices_inline()

    status: dict[str, Any] = {
        "ocr_engine": os.getenv("OCR_ENGINE", CONFIG.ocr_engine),
        "ocr_device": os.getenv("PB_OCR_DEVICE", os.getenv("OCR_DEVICE", CONFIG.ocr_device)),
        "paddle_use_gpu": CONFIG.paddle_use_gpu,
        "paddle_langs": os.getenv("OCR_PADDLE_LANG", CONFIG.paddle_langs),
        "paddle_dpi": os.getenv("OCR_PADDLE_DPI", str(CONFIG.paddle_dpi)),
        "cpu_threads": CONFIG.ocr_cpu_threads,
        "mkldnn": _should_enable_mkldnn(CONFIG.ocr_enable_mkldnn),
        "fallback_to_cpu": CONFIG.ocr_fallback_to_cpu,
        "compute_devices": compute_devices,
        "engines_installed": {
            "paddleocr": _pkg_installed("paddleocr"),
            "rapidocr_onnxruntime": _pkg_installed("rapidocr_onnxruntime"),
            "pytesseract": _pkg_installed("pytesseract"),
            "pymupdf": _pkg_installed("fitz"),
        },
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

    status["cloud_vision_key_set"] = bool(
        getattr(CONFIG, "api_key", None) and CONFIG.api_key != "local-no-key"
    )
    status["local_ai_servers"] = await _probe_all_local_ai()
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
    device: Annotated[
        str, Field(description="Compute device: 'auto' | 'gpu' | 'cuda:0' | 'mps' | 'cpu'")
    ] = "auto",
    engine: Annotated[
        str,
        Field(
            description="OCR engine: 'auto' | 'paddle' | 'rapidocr' | 'tesseract' | 'ollama' | 'vllm' | 'lmstudio' | 'llamacpp' | 'hybrid_local'"
        ),
    ] = "auto",
    local_ai_model: Annotated[
        Optional[str], Field(description="Optional model name for Local AI servers")
    ] = None,
    local_ai_url: Annotated[
        Optional[str], Field(description="Optional base URL override for Local AI server")
    ] = None,
    cpu_threads: Annotated[
        Optional[int], Field(ge=1, le=64, description="CPU thread count override for local OCR")
    ] = None,
) -> dict:
    """OCR a single image file using local OCR engines or Local AI vision (no cloud API key required)."""
    p = Path(image_path).expanduser()
    if not p.is_file():
        raise ValueError(f"Image not found: {p}")

    text, engine_used = await _run_ocr_on_bytes(
        p.read_bytes(),
        device=device,
        engine=engine,
        local_ai_model=local_ai_model,
        local_ai_url=local_ai_url,
        cpu_threads=cpu_threads,
    )
    if output_path:
        op = Path(output_path).expanduser()
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(text or "", encoding="utf-8")
    return {
        "file": p.name,
        "markdown": text or "",
        "chars": len(text or ""),
        "engine": engine_used,
        "device": device,
    }


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
    device: Annotated[
        str, Field(description="Compute device: 'auto' | 'gpu' | 'cuda:0' | 'mps' | 'cpu'")
    ] = "auto",
    engine: Annotated[
        str,
        Field(
            description="OCR engine: 'auto' | 'paddle' | 'rapidocr' | 'tesseract' | 'ollama' | 'vllm' | 'lmstudio' | 'llamacpp' | 'hybrid_local'"
        ),
    ] = "auto",
    local_ai_model: Annotated[
        Optional[str], Field(description="Optional model name for Local AI servers")
    ] = None,
    local_ai_url: Annotated[
        Optional[str], Field(description="Optional base URL override for Local AI server")
    ] = None,
    cpu_threads: Annotated[
        Optional[int], Field(ge=1, le=64, description="CPU thread count override for local OCR")
    ] = None,
    batch_size: Annotated[
        int, Field(ge=1, le=16, description="Concurrent pages to render & OCR per batch (default: 4)")
    ] = 4,
) -> dict:
    """Convert PDF to Markdown with batched concurrent rendering + OCR, preserving exact page order 1..N."""
    p = Path(pdf_path).expanduser()
    if not p.is_file():
        raise ValueError(f"PDF not found: {p}")
    data = p.read_bytes()

    from projectbrain.utils.pdf_render import (
        is_available as render_ok,
        page_count,
        render_page_png,
    )

    if not render_ok():
        raise RuntimeError("PDF renderer missing. Please run: pip install pymupdf")

    # Pre-extract the digital text layer (when not forcing OCR).
    layer: dict[int, str] = {}
    if not force_ocr:
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for i, page in enumerate(pdf.pages):
                    layer[i] = page.extract_text(x_tolerance=2) or ""
        except Exception:
            layer = {}

    n = page_count(data)
    page_bodies: dict[int, str] = {}
    ocr_indices: list[int] = []

    for i in range(n):
        txt = layer.get(i, "")
        if (not force_ocr) and txt and len(txt.strip()) >= 15:
            page_bodies[i] = txt.strip()
        else:
            ocr_indices.append(i)

    eff_batch = max(1, batch_size)
    sem = asyncio.Semaphore(eff_batch)
    engine_used = engine

    async def _ocr_single_rendered(idx: int, png: Optional[bytes]) -> tuple[int, str, str]:
        if not png:
            return idx, "_(no text detected)_", engine_used
        async with sem:
            ocr_txt, used = await _run_ocr_on_bytes(
                png,
                device=device,
                engine=engine,
                local_ai_model=local_ai_model,
                local_ai_url=local_ai_url,
                cpu_threads=cpu_threads,
            )
            return idx, (ocr_txt.strip() if ocr_txt else "_(no text detected)_"), used

    for start in range(0, len(ocr_indices), eff_batch):
        batch = ocr_indices[start : start + eff_batch]
        rendered_pngs = await asyncio.gather(
            *(asyncio.to_thread(render_page_png, data, idx, dpi) for idx in batch)
        )
        batch_results = await asyncio.gather(
            *(_ocr_single_rendered(idx, png) for idx, png in zip(batch, rendered_pngs))
        )
        for idx, body, used in batch_results:
            page_bodies[idx] = body
            engine_used = used

    parts: list[str] = []
    for i in range(n):
        parts.append(f"## Page {i + 1}\n")
        parts.append(page_bodies.get(i, "_(no text detected)_") + "\n")

    markdown = "\n".join(parts).strip()
    if output_path:
        op = Path(output_path).expanduser()
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(markdown, encoding="utf-8")
    return {
        "file": p.name,
        "pages": n,
        "ocr_pages": len(ocr_indices),
        "batch_size": eff_batch,
        "engine": engine_used,
        "device": device,
        "markdown": markdown,
        "output": output_path,
    }


def main() -> None:
    mcp.run()  # stdio (phu hop Claude Desktop/local)


if __name__ == "__main__":
    main()
