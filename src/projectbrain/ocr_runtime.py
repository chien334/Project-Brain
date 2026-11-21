"""Unified GPU/CPU Compute Manager & Local AI Vision/OCR Runtime for Project-Brain.

Provides:
- ``ComputeDeviceManager``: Safe hardware detection (CUDA, Apple MPS/Metal, ONNX
  Runtime providers, Tesseract, CPU/MKLDNN), device resolution, and fast async
  probing of local AI inference servers (Ollama, vLLM, LM Studio, llama.cpp).
- ``LocalVisionParser``: Multimodal OCR & Markdown structuring via Ollama
  ``/api/chat`` or OpenAI-compatible local servers (vLLM, LM Studio, llama.cpp),
  plus hybrid raw-OCR refinement into clean Markdown.
- ``UnifiedOCREngine``: Unified async/sync OCR facade across PaddleOCR (2.x/3.x
  with automatic GPU -> CPU fallback), RapidOCR (ONNX Runtime), Tesseract, Local
  VLMs, ``hybrid_local``, and smart ``auto`` cascade, plus concurrent batching.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import gc
import importlib
import importlib.util
import io
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from .ai.ollama import OllamaAdapter
from .core.config import env

logger = logging.getLogger(__name__)

# Serialize C++ predictor creation, eviction, and inference across concurrent threads.
_paddle_infer_lock = threading.RLock()
_rapidocr_infer_lock = threading.RLock()

_DEFAULT_OCR_PROMPT = (
    "You are an OCR and document-structuring engine.\n"
    "Extract ALL text from the image and output clean GitHub-Flavored Markdown.\n"
    "Preserve structure: headings, lists, tables, code blocks, formulas, and reading order.\n"
    "Do NOT translate. Do NOT add commentary. If there is no text, output an empty string.\n"
    "Output ONLY the Markdown."
)

_LOCAL_OPENAI_DEFAULT_URLS: Dict[str, str] = {
    "vllm": "http://localhost:8000/v1",
    "lmstudio": "http://localhost:1234/v1",
    "llamacpp": "http://localhost:8081/v1",
}


def _module_available(name: str) -> bool:
    """Return True if *name* is already imported or discoverable without importing it."""
    if name in sys.modules:
        return sys.modules[name] is not None
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _normalize_openai_base_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.endswith("/v1"):
        return cleaned
    return f"{cleaned}/v1"


def _is_gpu_or_runtime_error(exc: BaseException) -> bool:
    if isinstance(exc, ImportError):
        return False
    msg = str(exc).lower()
    if isinstance(exc, (RuntimeError, MemoryError, OSError)):
        return True
    gpu_tokens = (
        "cuda",
        "cudnn",
        "cublas",
        "tensorrt",
        "out of memory",
        "oom",
        "gpu",
        "device",
        "onnxruntime",
        "coreml",
        "hip",
        "pir",
        "mkldnn",
    )
    return any(tok in msg for tok in gpu_tokens)


def _is_mkldnn_error(exc: BaseException) -> bool:
    if not isinstance(exc, RuntimeError):
        return False
    msg = str(exc).lower()
    return any(tok in msg for tok in ("mkldnn", "onednn", "pir"))


def _to_bgr_ndarray(image_bytes: bytes):
    """Decode image bytes into a BGR numpy array expected by PaddleOCR/RapidOCR."""
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as im:
        arr = np.asarray(im.convert("RGB"))
    return arr[:, :, ::-1].copy()


def _poly_to_box(poly: Any) -> Tuple[float, float, float, float]:
    """Normalize a 4-point polygon or flat box into (x0, y0, x1, y1)."""
    if poly is None:
        return (0.0, 0.0, 0.0, 0.0)
    try:
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        return (min(xs), min(ys), max(xs), max(ys))
    except Exception:
        try:
            vals = [float(v) for v in list(poly)[:4]]
            if len(vals) == 4:
                return (vals[0], vals[1], vals[2], vals[3])
        except Exception:
            pass
    return (0.0, 0.0, 0.0, 0.0)


def _reconstruct_reading_order(
    items: Sequence[Tuple[str, float, Tuple[float, float, float, float]]]
) -> str:
    """Group OCR detections into lines sorted top-to-bottom, left-to-right."""
    if not items:
        return ""
    ordered = sorted(items, key=lambda it: (it[2][1], it[2][0]))
    heights = sorted(max(1.0, it[2][3] - it[2][1]) for it in ordered)
    line_h = heights[len(heights) // 2] or 1.0

    lines: List[List[Tuple[float, str]]] = []
    cur: List[Tuple[float, str]] = []
    cur_y: Optional[float] = None
    for text, _conf, (x0, y0, _x1, _y1) in ordered:
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

    out_lines: List[str] = []
    for line in lines:
        line.sort(key=lambda pair: pair[0])
        joined = " ".join(txt for _, txt in line if txt).strip()
        if joined:
            out_lines.append(joined)
    return "\n".join(out_lines).strip()


def _parse_paddle_v3(res: Any) -> Optional[List[Tuple[str, float, Tuple[float, float, float, float]]]]:
    """Parse PaddleOCR 3.x ``predict()`` output; return None if shape does not match."""
    if not res:
        return []
    items: List[Tuple[str, float, Tuple[float, float, float, float]]] = []
    for page in res:
        container = page
        getter = container.get if hasattr(container, "get") else None
        if getter is None:
            inner = getattr(container, "res", None)
            if inner is None:
                return None
            container = inner
            getter = container.get if hasattr(container, "get") else None
            if getter is None:
                return None
        texts = getter("rec_texts")
        if texts is None:
            return None
        scores = getter("rec_scores") or [1.0] * len(texts)
        polys = (
            getter("rec_polys")
            or getter("dt_polys")
            or getter("rec_boxes")
            or [None] * len(texts)
        )
        for txt, score, poly in zip(texts, scores, polys):
            if txt is None:
                continue
            items.append((str(txt), float(score), _poly_to_box(poly)))
    return items


def _parse_paddle_v2(res: Any) -> List[Tuple[str, float, Tuple[float, float, float, float]]]:
    """Parse PaddleOCR 2.x ``ocr()`` output."""
    items: List[Tuple[str, float, Tuple[float, float, float, float]]] = []
    if not res:
        return items

    first = res[0]
    single_page = False
    try:
        if first and isinstance(first[1], (list, tuple)) and isinstance(first[1][0], str):
            single_page = True
    except Exception:
        pass

    pages = [res] if single_page else res
    for page in pages:
        for line in page or []:
            try:
                box = line[0]
                text = line[1][0]
                conf = float(line[1][1])
                if text:
                    items.append((str(text), conf, _poly_to_box(box)))
            except Exception:
                continue
    return items


class ComputeDeviceManager:
    """Probes local compute hardware, resolves OCR device flags, and checks local AI endpoints."""

    @classmethod
    def detect_hardware(cls) -> Dict[str, Any]:
        """Safely probe CUDA, Apple MPS/Metal, ONNX providers, Tesseract, and CPU capabilities."""
        os_name = platform.system().lower()
        arch = platform.machine().lower()
        is_macos = os_name == "darwin"
        is_arm64 = arch in ("arm64", "aarch64")
        is_x86_64 = arch in ("x86_64", "amd64")
        cpu_count = os.cpu_count() or 1
        mkldnn_eligible = (not is_macos) and (not is_arm64) and is_x86_64
        if not mkldnn_eligible:
            os.environ["FLAGS_use_mkldnn"] = "0"

        paddle_cuda = False
        paddle_gpu_count = 0
        try:
            import paddle  # type: ignore

            if hasattr(paddle, "device") and hasattr(paddle.device, "is_compiled_with_cuda"):
                paddle_cuda = bool(paddle.device.is_compiled_with_cuda())
                if (
                    paddle_cuda
                    and hasattr(paddle.device, "cuda")
                    and hasattr(paddle.device.cuda, "device_count")
                ):
                    paddle_gpu_count = int(paddle.device.cuda.device_count() or 0)
                    if paddle_gpu_count <= 0:
                        paddle_cuda = False
        except Exception:
            pass

        torch_cuda = False
        torch_gpu_count = 0
        torch_mps = False
        gpu_names: List[str] = []
        try:
            import torch  # type: ignore

            if hasattr(torch, "cuda") and torch.cuda.is_available():
                torch_cuda = True
                torch_gpu_count = int(torch.cuda.device_count() or 0)
                for idx in range(torch_gpu_count):
                    try:
                        gpu_names.append(str(torch.cuda.get_device_name(idx)))
                    except Exception:
                        pass
            if (
                hasattr(torch, "backends")
                and hasattr(torch.backends, "mps")
                and torch.backends.mps.is_available()
            ):
                torch_mps = True
        except Exception:
            pass

        nvidia_smi_path = shutil.which("nvidia-smi")
        nvidia_smi_gpus: List[str] = []
        if nvidia_smi_path:
            try:
                proc = subprocess.run(
                    [
                        nvidia_smi_path,
                        "--query-gpu=name,memory.total",
                        "--format=csv,noheader",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                if proc.returncode == 0 and proc.stdout:
                    nvidia_smi_gpus = [
                        line.strip() for line in proc.stdout.splitlines() if line.strip()
                    ]
            except Exception:
                pass

        onnx_providers: List[str] = []
        onnxruntime_available = False
        try:
            import onnxruntime as ort  # type: ignore

            onnxruntime_available = True
            if hasattr(ort, "get_available_providers"):
                onnx_providers = [str(p) for p in (ort.get_available_providers() or [])]
        except Exception:
            pass

        tesseract_path = shutil.which("tesseract")
        pytesseract_available = _module_available("pytesseract")
        paddleocr_available = _module_available("paddleocr")
        rapidocr_available = _module_available("rapidocr_onnxruntime")

        cuda_device_count = max(paddle_gpu_count, torch_gpu_count, len(nvidia_smi_gpus))
        cuda_available = bool(
            paddle_cuda
            or torch_cuda
            or ("CUDAExecutionProvider" in onnx_providers)
            or bool(nvidia_smi_gpus)
        )
        apple_silicon = bool(is_macos and is_arm64)

        return {
            "os": os_name,
            "arch": arch,
            "is_macos": is_macos,
            "is_arm64": is_arm64,
            "is_x86_64": is_x86_64,
            "cpu_count": cpu_count,
            "mkldnn_eligible": mkldnn_eligible,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "paddle_cuda": paddle_cuda,
            "torch_cuda": torch_cuda,
            "gpu_names": gpu_names,
            "nvidia_smi_gpus": nvidia_smi_gpus,
            "apple_silicon": apple_silicon,
            "mps_available": bool(torch_mps),
            "torch_mps": bool(torch_mps),
            "metal_available": apple_silicon,
            "onnxruntime_available": onnxruntime_available,
            "onnx_providers": onnx_providers,
            "tesseract_available": bool(tesseract_path),
            "tesseract_path": tesseract_path,
            "pytesseract_available": pytesseract_available,
            "paddleocr_available": paddleocr_available,
            "rapidocr_available": rapidocr_available,
        }

    @classmethod
    def resolve_device(
        cls,
        requested: Optional[str] = "auto",
        engine: str = "paddle",
        hardware: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Resolve concrete runtime device flags for the target OCR/inference engine."""
        hw = hardware if hardware is not None else cls.detect_hardware()
        eng = (engine or "paddle").strip().lower()
        req_raw = requested if requested is not None else getattr(env, "ocr_device", "auto")
        req = (str(req_raw).strip().lower() or "auto")

        gpu_id = int(getattr(env, "ocr_gpu_id", 0) or 0)
        base_req = req
        if ":" in req:
            prefix, suffix = req.split(":", 1)
            base_req = prefix.strip()
            try:
                gpu_id = max(0, int(suffix.strip()))
            except ValueError:
                pass

        gpu_mem_mb = int(getattr(env, "ocr_gpu_mem_mb", 2000) or 2000)
        use_tensorrt = bool(getattr(env, "ocr_use_tensorrt", False))
        precision = str(getattr(env, "ocr_precision", "fp32") or "fp32").strip().lower()
        if precision not in ("fp32", "fp16", "int8"):
            precision = "fp32"
        cpu_threads = max(
            1,
            int(getattr(env, "ocr_cpu_threads", min(8, hw.get("cpu_count", 4))) or 4),
        )
        fallback_to_cpu = bool(getattr(env, "ocr_fallback_to_cpu", True))

        mkldnn_cfg = str(getattr(env, "ocr_enable_mkldnn", "auto") or "auto").strip().lower()
        if mkldnn_cfg == "auto":
            enable_mkldnn = bool(hw.get("mkldnn_eligible", False))
        else:
            enable_mkldnn = mkldnn_cfg in ("1", "true", "yes", "on")

        available_onnx = list(hw.get("onnx_providers") or [])
        has_paddle_cuda = bool(hw.get("paddle_cuda", False))
        has_any_cuda = bool(hw.get("cuda_available", False))
        has_coreml = "CoreMLExecutionProvider" in available_onnx or bool(
            hw.get("apple_silicon", False)
        )
        has_mps = bool(hw.get("mps_available", False)) or bool(
            hw.get("apple_silicon", False)
        )

        use_gpu = False
        device_str = "cpu"

        if base_req == "cpu":
            use_gpu = False
            device_str = "cpu"
        elif base_req in ("gpu", "cuda"):
            cuda_ok = has_paddle_cuda if eng == "paddle" else has_any_cuda
            if cuda_ok or not fallback_to_cpu:
                use_gpu = True
                device_str = f"gpu:{gpu_id}" if eng == "paddle" else f"cuda:{gpu_id}"
            else:
                use_gpu = False
                device_str = "cpu"
        elif base_req in ("mps", "coreml"):
            if eng == "paddle":
                # PaddleOCR does not support Apple MPS/CoreML execution; keep CPU cleanly.
                use_gpu = False
                device_str = "cpu"
            elif (base_req == "coreml" and has_coreml) or (base_req == "mps" and has_mps) or not fallback_to_cpu:
                use_gpu = True
                device_str = base_req
            else:
                use_gpu = False
                device_str = "cpu"
        else:  # "auto" (or unrecognized fallback to auto)
            if eng == "paddle":
                if has_paddle_cuda:
                    use_gpu = True
                    device_str = f"gpu:{gpu_id}"
                else:
                    use_gpu = False
                    device_str = "cpu"
            elif eng in ("rapidocr", "onnx"):
                if has_any_cuda and (
                    not available_onnx or "CUDAExecutionProvider" in available_onnx
                ):
                    use_gpu = True
                    device_str = f"cuda:{gpu_id}"
                elif has_coreml and "CoreMLExecutionProvider" in available_onnx:
                    use_gpu = True
                    device_str = "coreml"
                else:
                    use_gpu = False
                    device_str = "cpu"
            elif eng == "tesseract":
                use_gpu = False
                device_str = "cpu"
            else:
                if has_any_cuda:
                    use_gpu = True
                    device_str = f"cuda:{gpu_id}"
                elif has_mps:
                    use_gpu = True
                    device_str = "mps"
                else:
                    use_gpu = False
                    device_str = "cpu"

        if use_gpu:
            if device_str in ("mps", "coreml"):
                preferred = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
            else:
                preferred = []
                if use_tensorrt:
                    preferred.append("TensorrtExecutionProvider")
                preferred.extend(["CUDAExecutionProvider", "CPUExecutionProvider"])
            if available_onnx:
                onnx_providers = [
                    p for p in preferred if p in available_onnx or p == "CPUExecutionProvider"
                ]
                if "CPUExecutionProvider" not in onnx_providers:
                    onnx_providers.append("CPUExecutionProvider")
            else:
                onnx_providers = preferred
        else:
            onnx_providers = ["CPUExecutionProvider"]

        return {
            "use_gpu": use_gpu,
            "device_str": device_str,
            "gpu_id": gpu_id,
            "gpu_mem_mb": gpu_mem_mb,
            "use_tensorrt": use_tensorrt,
            "precision": precision,
            "cpu_threads": cpu_threads,
            "enable_mkldnn": enable_mkldnn,
            "onnx_providers": onnx_providers,
            "fallback_to_cpu": fallback_to_cpu,
        }

    @classmethod
    async def probe_local_ai_endpoints(
        cls,
        timeout: float = 1.5,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> Dict[str, Any]:
        """Probe Ollama, vLLM, LM Studio, llama.cpp, and configured PB_LOCAL_AI_URL."""
        ollama_base = (getattr(env, "ollama_url", None) or "http://localhost:11434").rstrip("/")
        targets: Dict[str, Tuple[str, str, str]] = {
            "ollama": (ollama_base, f"{ollama_base}/api/tags", "ollama"),
            "vllm": (
                _LOCAL_OPENAI_DEFAULT_URLS["vllm"],
                f"{_LOCAL_OPENAI_DEFAULT_URLS['vllm']}/models",
                "openai",
            ),
            "lmstudio": (
                _LOCAL_OPENAI_DEFAULT_URLS["lmstudio"],
                f"{_LOCAL_OPENAI_DEFAULT_URLS['lmstudio']}/models",
                "openai",
            ),
            "llamacpp": (
                _LOCAL_OPENAI_DEFAULT_URLS["llamacpp"],
                f"{_LOCAL_OPENAI_DEFAULT_URLS['llamacpp']}/models",
                "openai",
            ),
        }

        custom_base = _normalize_openai_base_url(
            getattr(env, "local_ai_base_url", "") or ""
        )
        if custom_base:
            targets["custom"] = (custom_base, f"{custom_base}/models", "openai")

        client_kwargs: Dict[str, Any] = {"timeout": float(timeout)}
        if transport is not None:
            client_kwargs["transport"] = transport

        api_key = getattr(env, "local_ai_api_key", None) or "local-no-key"

        async def _probe_one(
            client: httpx.AsyncClient,
            provider: str,
            base_url: str,
            probe_url: str,
            kind: str,
        ) -> Tuple[str, Dict[str, Any]]:
            headers = {}
            if kind == "openai":
                headers["Authorization"] = f"Bearer {api_key}"
            try:
                res = await client.get(probe_url, headers=headers)
                if res.status_code != 200:
                    return provider, {
                        "provider": provider,
                        "reachable": False,
                        "base_url": base_url,
                        "url": probe_url,
                        "models": [],
                        "error": f"HTTP {res.status_code}",
                    }
                data = res.json() or {}
                if kind == "ollama":
                    models = [
                        str(item["name"])
                        for item in (data.get("models") or [])
                        if isinstance(item, dict) and item.get("name")
                    ]
                else:
                    models = [
                        str(item["id"])
                        for item in (data.get("data") or [])
                        if isinstance(item, dict) and item.get("id")
                    ]
                return provider, {
                    "provider": provider,
                    "reachable": True,
                    "base_url": base_url,
                    "url": probe_url,
                    "models": models,
                    "error": None,
                }
            except Exception as exc:
                return provider, {
                    "provider": provider,
                    "reachable": False,
                    "base_url": base_url,
                    "url": probe_url,
                    "models": [],
                    "error": str(exc),
                }

        async with httpx.AsyncClient(**client_kwargs) as client:
            pairs = await asyncio.gather(
                *(
                    _probe_one(client, provider, base_url, probe_url, kind)
                    for provider, (base_url, probe_url, kind) in targets.items()
                )
            )
        return dict(pairs)


class LocalVisionParser:
    """Multimodal OCR & Markdown refiner supporting Ollama and OpenAI-compatible local servers."""

    SUPPORTED_PROVIDERS = (
        "ollama",
        "vllm",
        "lmstudio",
        "llamacpp",
        "openai_compatible",
    )

    def __init__(
        self,
        provider: str = "ollama",
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        prov = (provider or "ollama").strip().lower()
        if prov == "custom":
            prov = "openai_compatible"
        if prov not in self.SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported local vision provider {provider!r}; "
                f"expected one of {self.SUPPORTED_PROVIDERS}"
            )
        self.provider = prov
        self.timeout = float(timeout)
        self.transport = transport

        if prov == "ollama":
            resolved_base = (
                base_url
                or getattr(env, "ollama_url", None)
                or "http://localhost:11434"
            ).rstrip("/")
            resolved_model = (
                model
                or getattr(env, "ollama_vision_model", None)
                or "qwen2.5vl:7b"
            )
        else:
            raw_base = (
                base_url
                or getattr(env, "local_ai_base_url", "")
                or _LOCAL_OPENAI_DEFAULT_URLS.get(prov, "http://localhost:8000/v1")
            )
            resolved_base = _normalize_openai_base_url(raw_base)
            resolved_model = (
                model
                or getattr(env, "local_ai_model", "")
                or getattr(env, "ollama_vision_model", None)
                or "qwen2.5vl:7b"
            )

        self.base_url = resolved_base
        self.model = resolved_model
        self.api_key = (
            api_key
            or getattr(env, "local_ai_api_key", None)
            or "local-no-key"
        )

    def _client(self) -> httpx.AsyncClient:
        kwargs: Dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

    async def _openai_chat_completions(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 0.1,
    ) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key or 'local-no-key'}",
            "Content-Type": "application/json",
        }
        async with self._client() as client:
            res = await client.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                },
            )
            if res.status_code != 200:
                raise RuntimeError(
                    f"Local AI ({self.provider}) HTTP {res.status_code}: {res.text[:300]}"
                )
            data = res.json() or {}
            choices = data.get("choices") or []
            if not choices:
                return ""
            msg = choices[0].get("message") or {}
            return str(msg.get("content") or "").strip()

    async def parse_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
        prompt: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        """Extract structured Markdown from image bytes via the configured local VLM."""
        if not image_bytes:
            return ""
        target_model = model or self.model
        prompt_text = prompt or _DEFAULT_OCR_PROMPT

        if self.provider == "ollama":
            adapter = OllamaAdapter(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self.transport,
            )
            text = await adapter.vision_chat(
                prompt_text, [image_bytes], model=target_model
            )
            return (text or "").strip()

        b64 = base64.b64encode(image_bytes).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                    },
                ],
            }
        ]
        return await self._openai_chat_completions(messages, model=target_model)

    async def refine_ocr_markdown(
        self,
        raw_ocr_text: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/png",
        model: Optional[str] = None,
    ) -> str:
        """Refine raw OCR text (and optionally the source image) into clean Markdown."""
        raw_clean = (raw_ocr_text or "").strip()
        if not raw_clean and not image_bytes:
            return ""

        if image_bytes:
            refine_prompt = (
                "You are an OCR post-processor and technical Markdown formatter.\n"
                "Use the attached image and the raw OCR draft below to produce accurate, "
                "clean GitHub-Flavored Markdown. Fix broken tables, formulas, headings, "
                "lists, and code blocks without inventing content or translating.\n"
                "Output ONLY the Markdown.\n\n"
                f"Raw OCR Draft:\n```\n{raw_clean}\n```"
            )
            try:
                refined = await self.parse_image(
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    prompt=refine_prompt,
                    model=model,
                )
                return refined.strip() or raw_clean
            except Exception as exc:
                logger.warning("Local vision refinement failed (%s); returning raw OCR text.", exc)
                return raw_clean

        text_prompt = (
            "You are an OCR post-processor. Reformat the raw OCR text below into clean "
            "GitHub-Flavored Markdown, repairing broken tables, headings, lists, code "
            "blocks, and math notation while preserving every word faithfully.\n"
            "Do NOT translate or add commentary. Output ONLY the Markdown.\n\n"
            f"Raw OCR Text:\n{raw_clean}"
        )
        target_model = model or (
            getattr(env, "ollama_model", None)
            if self.provider == "ollama"
            else self.model
        ) or self.model

        try:
            if self.provider == "ollama":
                adapter = OllamaAdapter(
                    base_url=self.base_url,
                    timeout=self.timeout,
                    transport=self.transport,
                )
                refined = await adapter.chat(
                    [{"role": "user", "content": text_prompt}],
                    model=target_model,
                )
            else:
                refined = await self._openai_chat_completions(
                    [{"role": "user", "content": text_prompt}],
                    model=target_model,
                )
            return (refined or "").strip() or raw_clean
        except Exception as exc:
            logger.warning("Local text refinement failed (%s); returning raw OCR text.", exc)
            return raw_clean


class UnifiedOCREngine:
    """Unified async/sync OCR engine with GPU/CPU management and local/cloud fallbacks."""

    _HYBRID_CONFIDENCE_THRESHOLD = 0.82

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._transport = transport
        self._paddle_cache: Dict[Tuple[Any, ...], Any] = {}
        self._rapidocr_cache: Dict[Tuple[str, ...], Any] = {}
        self._cache_lock = threading.Lock()

    @staticmethod
    def _needs_hybrid_refinement(text: str, confidence: float) -> bool:
        if confidence < UnifiedOCREngine._HYBRID_CONFIDENCE_THRESHOLD:
            return True
        stripped = (text or "").strip()
        if not stripped:
            return True
        lines = [ln for ln in stripped.splitlines() if ln.strip()]
        if "|" in stripped or "\t" in stripped:
            return True
        if len(lines) >= 6:
            short_lines = sum(1 for ln in lines if len(ln.strip()) <= 4)
            if short_lines / max(1, len(lines)) >= 0.45:
                return True
        return False

    def _paddle_cache_key(self, lang: str, resolved: Dict[str, Any]) -> Tuple[Any, ...]:
        return (
            lang,
            resolved["device_str"],
            resolved["gpu_id"],
            resolved["precision"],
            resolved["cpu_threads"],
            resolved["enable_mkldnn"],
        )

    def _evict_paddle_engine(self, lang: str, resolved: Dict[str, Any]) -> None:
        key = self._paddle_cache_key(lang, resolved)
        with _paddle_infer_lock:
            with self._cache_lock:
                removed = self._paddle_cache.pop(key, None)
            if resolved.get("use_gpu"):
                del removed
                try:
                    gc.collect()
                    import paddle  # type: ignore

                    if (
                        hasattr(paddle, "device")
                        and hasattr(paddle.device, "cuda")
                        and hasattr(paddle.device.cuda, "empty_cache")
                    ):
                        paddle.device.cuda.empty_cache()
                except Exception:
                    pass

    @staticmethod
    def _build_paddle_engine(lang: str, resolved: Dict[str, Any]) -> Any:
        enable_mkldnn = bool(resolved["enable_mkldnn"])
        if not enable_mkldnn:
            os.environ["FLAGS_use_mkldnn"] = "0"
        from paddleocr import PaddleOCR  # type: ignore

        use_gpu = bool(resolved["use_gpu"])
        device_str = resolved["device_str"] if use_gpu else "cpu"
        cpu_threads = int(resolved["cpu_threads"])

        v3_common = dict(
            lang=lang,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        if use_gpu:
            attempts = [
                dict(
                    **v3_common,
                    device=device_str,
                    cpu_threads=cpu_threads,
                    enable_mkldnn=enable_mkldnn,
                ),
                dict(**v3_common, device=device_str, enable_mkldnn=enable_mkldnn),
                dict(**v3_common, device=device_str),
                dict(
                    lang=lang,
                    use_gpu=True,
                    gpu_id=int(resolved["gpu_id"]),
                    gpu_mem=int(resolved["gpu_mem_mb"]),
                    use_tensorrt=bool(resolved["use_tensorrt"]),
                    precision=resolved["precision"],
                    cpu_threads=cpu_threads,
                    enable_mkldnn=enable_mkldnn,
                    use_angle_cls=False,
                    show_log=False,
                ),
                dict(
                    lang=lang,
                    use_gpu=True,
                    gpu_id=int(resolved["gpu_id"]),
                    enable_mkldnn=enable_mkldnn,
                    use_angle_cls=False,
                ),
                dict(
                    lang=lang,
                    use_gpu=True,
                    gpu_id=int(resolved["gpu_id"]),
                    use_angle_cls=False,
                ),
                dict(lang=lang, use_gpu=True, enable_mkldnn=enable_mkldnn),
                dict(lang=lang, use_gpu=True),
            ]
        else:
            attempts = [
                dict(
                    **v3_common,
                    device="cpu",
                    cpu_threads=cpu_threads,
                    enable_mkldnn=enable_mkldnn,
                ),
                dict(**v3_common, device="cpu", enable_mkldnn=enable_mkldnn),
                dict(**v3_common, enable_mkldnn=enable_mkldnn),
                dict(**v3_common),
                dict(
                    lang=lang,
                    use_gpu=False,
                    cpu_threads=cpu_threads,
                    enable_mkldnn=enable_mkldnn,
                    use_angle_cls=False,
                    show_log=False,
                ),
                dict(
                    lang=lang,
                    use_gpu=False,
                    enable_mkldnn=enable_mkldnn,
                    use_angle_cls=False,
                ),
                dict(lang=lang, use_gpu=False, use_angle_cls=False),
                dict(lang=lang, enable_mkldnn=enable_mkldnn),
                dict(lang=lang),
            ]

        last_type_err: Optional[TypeError] = None
        for kwargs in attempts:
            try:
                return PaddleOCR(**kwargs)
            except TypeError as exc:
                last_type_err = exc
                continue
        raise RuntimeError(f"Cannot initialise PaddleOCR(lang={lang!r}): {last_type_err}")

    def _get_paddle_engine(self, lang: str, resolved: Dict[str, Any]) -> Any:
        key = self._paddle_cache_key(lang, resolved)
        with _paddle_infer_lock:
            with self._cache_lock:
                cached = self._paddle_cache.get(key)
                if cached is None:
                    cached = self._build_paddle_engine(lang, resolved)
                    self._paddle_cache[key] = cached
            return cached

    @staticmethod
    def _infer_paddle(engine: Any, img_ndarray: Any) -> List[Tuple[str, float, Tuple[float, float, float, float]]]:
        with _paddle_infer_lock:
            if hasattr(engine, "predict"):
                try:
                    parsed = _parse_paddle_v3(engine.predict(img_ndarray))
                    if parsed is not None:
                        return parsed
                except Exception as exc:
                    if _is_gpu_or_runtime_error(exc) or not hasattr(engine, "ocr"):
                        raise
            try:
                raw = engine.ocr(img_ndarray)
            except TypeError:
                raw = engine.ocr(img_ndarray, cls=True)
            return _parse_paddle_v2(raw)

    def _run_paddle_pass(
        self,
        img_ndarray: Any,
        langs: List[str],
        resolved: Dict[str, Any],
    ) -> Tuple[str, float]:
        best_text = ""
        best_conf = 0.0
        best_score = -1.0
        for code in langs:
            engine = self._get_paddle_engine(code, resolved)
            items = self._infer_paddle(engine, img_ndarray)
            if not items:
                continue
            avg_conf = sum(c for _, c, _ in items) / len(items)
            if avg_conf > best_score:
                best_score = avg_conf
                best_conf = round(avg_conf, 4)
                best_text = _reconstruct_reading_order(items)
        return best_text, best_conf

    def _extract_paddle_blocking(
        self,
        image_bytes: bytes,
        lang: Optional[str],
        device: Optional[str],
    ) -> Dict[str, Any]:
        raw_lang = (
            lang
            or getattr(env, "ocr_paddle_lang", None)
            or os.getenv("OCR_PADDLE_LANG", "ch")
            or "ch"
        )
        langs = [part.strip() for part in str(raw_lang).split(",") if part.strip()] or ["ch"]
        resolved = ComputeDeviceManager.resolve_device(
            requested=device, engine="paddle"
        )

        # Warm/validate engine before decoding image bytes so absent packages fast-fail.
        default_pass = getattr(self._run_paddle_pass, "__func__", None) is UnifiedOCREngine._run_paddle_pass

        img_ndarray_cache: Optional[Any] = None

        def _get_img() -> Any:
            nonlocal img_ndarray_cache
            if img_ndarray_cache is None:
                img_ndarray_cache = _to_bgr_ndarray(image_bytes)
            return img_ndarray_cache

        def _warm_and_run(cfg: Dict[str, Any]) -> Tuple[str, float]:
            if default_pass:
                self._get_paddle_engine(langs[0], cfg)
            return self._run_paddle_pass(_get_img(), langs, cfg)

        def _run_cpu_with_mkldnn_retry(cpu_cfg: Dict[str, Any]) -> Tuple[str, float, str, bool]:
            try:
                t, c = _warm_and_run(cpu_cfg)
                return t, c, cpu_cfg["device_str"], False
            except RuntimeError as cpu_exc:
                if cpu_cfg.get("enable_mkldnn") and _is_mkldnn_error(cpu_exc):
                    logger.warning(
                        "PaddleOCR CPU MKLDNN/oneDNN/PIR execution failed (%s); evicting and retrying with enable_mkldnn=False.",
                        cpu_exc,
                    )
                    for code in langs:
                        self._evict_paddle_engine(code, cpu_cfg)
                    no_mkldnn_cfg = dict(cpu_cfg, enable_mkldnn=False)
                    os.environ["FLAGS_use_mkldnn"] = "0"
                    t, c = _warm_and_run(no_mkldnn_cfg)
                    return t, c, no_mkldnn_cfg["device_str"], True
                raise

        fallback_triggered = False
        if not resolved["use_gpu"]:
            text, conf, device_used, fallback_triggered = _run_cpu_with_mkldnn_retry(resolved)
        else:
            try:
                text, conf = _warm_and_run(resolved)
                device_used = resolved["device_str"]
            except Exception as exc:
                if resolved["fallback_to_cpu"] and _is_gpu_or_runtime_error(exc):
                    logger.warning(
                        "PaddleOCR GPU execution failed (%s); evicting GPU engine and falling back to CPU.",
                        exc,
                    )
                    for code in langs:
                        self._evict_paddle_engine(code, resolved)
                    cpu_resolved = ComputeDeviceManager.resolve_device(
                        requested="cpu", engine="paddle"
                    )
                    text, conf, device_used, _ = _run_cpu_with_mkldnn_retry(cpu_resolved)
                    fallback_triggered = True
                else:
                    raise

        return {
            "text": text,
            "engine_used": "paddle",
            "device_used": device_used,
            "confidence": conf,
            "fallback_triggered": fallback_triggered,
        }

    def _get_rapidocr_engine(self, providers: Sequence[str]) -> Any:
        key = tuple(providers)
        with _rapidocr_infer_lock:
            with self._cache_lock:
                cached = self._rapidocr_cache.get(key)
                if cached is not None:
                    return cached

                from rapidocr_onnxruntime import RapidOCR  # type: ignore

                use_cuda = any("CUDA" in p or "Tensorrt" in p for p in providers)
                attempts = [
                    dict(providers=list(providers)),
                    dict(det_use_cuda=use_cuda, cls_use_cuda=use_cuda, rec_use_cuda=use_cuda),
                    dict(),
                ]
                last_type_err: Optional[TypeError] = None
                eng = None
                for kwargs in attempts:
                    try:
                        eng = RapidOCR(**kwargs)
                        break
                    except TypeError as exc:
                        last_type_err = exc
                if eng is None:
                    raise RuntimeError(f"Cannot initialise RapidOCR: {last_type_err}")
                self._rapidocr_cache[key] = eng
                return eng

    def _extract_rapidocr_blocking(
        self,
        image_bytes: bytes,
        device: Optional[str],
    ) -> Dict[str, Any]:
        resolved = ComputeDeviceManager.resolve_device(
            requested=device, engine="rapidocr"
        )

        img_ndarray_cache: Optional[Any] = None

        def _get_img() -> Any:
            nonlocal img_ndarray_cache
            if img_ndarray_cache is None:
                img_ndarray_cache = _to_bgr_ndarray(image_bytes)
            return img_ndarray_cache

        fallback_triggered = False

        def _run_with(res_cfg: Dict[str, Any]) -> Tuple[str, float]:
            with _rapidocr_infer_lock:
                eng = self._get_rapidocr_engine(res_cfg["onnx_providers"])
                out = eng(_get_img())
            raw_items = out[0] if isinstance(out, tuple) else out
            parsed: List[Tuple[str, float, Tuple[float, float, float, float]]] = []
            for row in raw_items or []:
                try:
                    box, text, score = row[0], row[1], float(row[2])
                    if text:
                        parsed.append((str(text), score, _poly_to_box(box)))
                except Exception:
                    continue
            if not parsed:
                return "", 0.0
            avg_conf = round(sum(c for _, c, _ in parsed) / len(parsed), 4)
            return _reconstruct_reading_order(parsed), avg_conf

        try:
            text, conf = _run_with(resolved)
            device_used = resolved["device_str"]
        except Exception as exc:
            if resolved["use_gpu"] and resolved["fallback_to_cpu"] and _is_gpu_or_runtime_error(exc):
                logger.warning(
                    "RapidOCR accelerator execution failed (%s); falling back to CPU.",
                    exc,
                )
                with _rapidocr_infer_lock:
                    with self._cache_lock:
                        self._rapidocr_cache.pop(tuple(resolved["onnx_providers"]), None)
                cpu_resolved = ComputeDeviceManager.resolve_device(
                    requested="cpu", engine="rapidocr"
                )
                text, conf = _run_with(cpu_resolved)
                device_used = cpu_resolved["device_str"]
                fallback_triggered = True
            else:
                raise

        return {
            "text": text,
            "engine_used": "rapidocr",
            "device_used": device_used,
            "confidence": conf,
            "fallback_triggered": fallback_triggered,
        }

    @staticmethod
    def _extract_tesseract_blocking(
        image_bytes: bytes,
        lang: Optional[str],
    ) -> Dict[str, Any]:
        tess_lang = (lang or "eng").replace(",", "+")
        try:
            import pytesseract  # type: ignore
            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as im:
                text = pytesseract.image_to_string(im, lang=tess_lang).strip()
                conf = 0.85 if text else 0.0
                try:
                    data = pytesseract.image_to_data(
                        im, lang=tess_lang, output_type=pytesseract.Output.DICT
                    )
                    confs = [
                        float(c) / 100.0
                        for c in (data.get("conf") or [])
                        if str(c).strip() not in ("", "-1") and float(c) >= 0
                    ]
                    if confs:
                        conf = round(sum(confs) / len(confs), 4)
                except Exception:
                    pass
                return {
                    "text": text,
                    "engine_used": "tesseract",
                    "device_used": "cpu",
                    "confidence": conf,
                    "fallback_triggered": False,
                }
        except ImportError:
            pass

        tess_bin = shutil.which("tesseract")
        if not tess_bin:
            raise RuntimeError("Neither pytesseract nor tesseract binary is installed.")
        proc = subprocess.run(
            [tess_bin, "stdin", "stdout", "-l", tess_lang],
            input=image_bytes,
            capture_output=True,
            timeout=60.0,
        )
        if proc.returncode != 0:
            err_msg = proc.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"tesseract CLI failed ({proc.returncode}): {err_msg}")
        text = proc.stdout.decode("utf-8", errors="replace").strip()
        return {
            "text": text,
            "engine_used": "tesseract",
            "device_used": "cpu",
            "confidence": 0.8 if text else 0.0,
            "fallback_triggered": False,
        }

    async def _extract_local_vlm(
        self,
        image_bytes: bytes,
        mime_type: str,
        provider: str,
        model: Optional[str],
        base_url: Optional[str],
        device: Optional[str],
        fallback_triggered: bool = False,
    ) -> Dict[str, Any]:
        parser = LocalVisionParser(
            provider=provider,
            model=model,
            base_url=base_url,
            transport=self._transport,
        )
        text = await parser.parse_image(image_bytes=image_bytes, mime_type=mime_type)
        resolved = ComputeDeviceManager.resolve_device(
            requested=device, engine=provider
        )
        return {
            "text": text,
            "engine_used": provider,
            "device_used": resolved["device_str"],
            "confidence": 0.9 if text.strip() else 0.0,
            "fallback_triggered": fallback_triggered,
        }

    async def _extract_cloud_vision(
        self,
        image_bytes: bytes,
        mime_type: str,
        fallback_triggered: bool = False,
    ) -> Dict[str, Any]:
        # 1) Reuse extension vision_client when LLM_API_KEY is configured
        if os.getenv("LLM_API_KEY", "").strip():
            try:
                from extensions_mcp.image_to_markdown.vision_client import (
                    DEFAULT_PROMPT,
                    extract_text_from_image,
                )
                from extensions_mcp.image_to_markdown.config import CONFIG

                if getattr(CONFIG, "api_key", None):
                    text = await extract_text_from_image(
                        image_bytes, mime_type, DEFAULT_PROMPT, CONFIG.model
                    )
                    return {
                        "text": (text or "").strip(),
                        "engine_used": "vision",
                        "device_used": "cloud",
                        "confidence": 0.92 if (text or "").strip() else 0.0,
                        "fallback_triggered": fallback_triggered,
                    }
            except Exception as exc:
                logger.debug("Extension vision_client unavailable/failed: %s", exc)

        # 2) OpenAI / OpenAI-compatible cloud key in Project-Brain env
        openai_key = getattr(env, "openai_key", "") or ""
        if openai_key:
            base = (getattr(env, "openai_base_url", None) or "https://api.openai.com/v1").rstrip("/")
            model = getattr(env, "openai_model", None) or "gpt-4o-mini"
            b64 = base64.b64encode(image_bytes).decode("ascii")
            kwargs: Dict[str, Any] = {"timeout": 120.0}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            async with httpx.AsyncClient(**kwargs) as client:
                res = await client.post(
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": _DEFAULT_OCR_PROMPT},
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                                    },
                                ],
                            }
                        ],
                    },
                )
                if res.status_code != 200:
                    raise RuntimeError(f"Cloud OpenAI vision HTTP {res.status_code}: {res.text[:200]}")
                data = res.json() or {}
                text = (
                    ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                ).strip()
                return {
                    "text": text,
                    "engine_used": "vision",
                    "device_used": "cloud",
                    "confidence": 0.92 if text else 0.0,
                    "fallback_triggered": fallback_triggered,
                }

        # 3) Gemini cloud key in Project-Brain env
        gemini_key = getattr(env, "gemini_key", "") or ""
        if gemini_key:
            from google import genai  # type: ignore
            from google.genai import types as genai_types  # type: ignore

            model = getattr(env, "gemini_model", None) or "gemini-2.5-flash"
            client = genai.Client(api_key=gemini_key)
            resp = await client.aio.models.generate_content(
                model=model,
                contents=[
                    genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    _DEFAULT_OCR_PROMPT,
                ],
            )
            text = (getattr(resp, "text", None) or "").strip()
            return {
                "text": text,
                "engine_used": "vision",
                "device_used": "cloud",
                "confidence": 0.92 if text else 0.0,
                "fallback_triggered": fallback_triggered,
            }

        raise RuntimeError("No cloud vision API key configured.")

    async def _pick_reachable_local_vlm(
        self,
        preferred_provider: Optional[str] = None,
    ) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
        """Return (provider, base_url, model) for the first reachable local AI server."""
        if preferred_provider:
            prov = preferred_provider.strip().lower()
            return (prov, None, None)

        endpoints = await ComputeDeviceManager.probe_local_ai_endpoints(
            timeout=1.5, transport=self._transport
        )
        order = ("ollama", "vllm", "lmstudio", "llamacpp", "custom")
        for key in order:
            info = endpoints.get(key)
            if not info or not info.get("reachable"):
                continue
            provider = "openai_compatible" if key == "custom" else key
            models = info.get("models") or []
            chosen_model: Optional[str] = None
            if provider == "ollama":
                configured = getattr(env, "ollama_vision_model", None)
                if configured and configured in models:
                    chosen_model = configured
                else:
                    for m in models:
                        if any(
                            h in m.lower()
                            for h in ("vl", "llava", "minicpm-v", "gemma3", "vision", "moondream")
                        ):
                            chosen_model = m
                            break
                    if chosen_model is None and models:
                        chosen_model = configured or models[0]
            elif models:
                chosen_model = getattr(env, "local_ai_model", None) or models[0]
            return (provider, info.get("base_url"), chosen_model)
        return None

    async def _run_hybrid_local(
        self,
        image_bytes: bytes,
        mime_type: str,
        device: Optional[str],
        lang: Optional[str],
        local_ai_provider: Optional[str],
        local_ai_model: Optional[str],
        local_ai_url: Optional[str],
    ) -> Dict[str, Any]:
        base_res: Optional[Dict[str, Any]] = None
        fallback_triggered = False

        for fast_engine in ("paddle", "rapidocr"):
            try:
                if fast_engine == "paddle":
                    base_res = await asyncio.to_thread(
                        self._extract_paddle_blocking, image_bytes, lang, device
                    )
                else:
                    base_res = await asyncio.to_thread(
                        self._extract_rapidocr_blocking, image_bytes, device
                    )
                fallback_triggered = bool(base_res.get("fallback_triggered", False))
                break
            except Exception as exc:
                logger.debug("hybrid_local %s pass failed: %s", fast_engine, exc)
                fallback_triggered = True

        raw_text = (base_res or {}).get("text", "")
        raw_conf = float((base_res or {}).get("confidence", 0.0))
        base_engine = (base_res or {}).get("engine_used", "none")
        base_device = (base_res or {}).get("device_used", "cpu")

        # Resolve local AI provider for refinement
        provider = local_ai_provider
        resolved_url = local_ai_url
        resolved_model = local_ai_model
        if not provider:
            picked = await self._pick_reachable_local_vlm()
            if picked is not None:
                provider, picked_url, picked_model = picked
                resolved_url = resolved_url or picked_url
                resolved_model = resolved_model or picked_model

        if provider:
            try:
                parser = LocalVisionParser(
                    provider=provider,
                    model=resolved_model,
                    base_url=resolved_url,
                    transport=self._transport,
                )
                refined = await parser.refine_ocr_markdown(
                    raw_ocr_text=raw_text,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    model=resolved_model,
                )
                if refined.strip():
                    return {
                        "text": refined.strip(),
                        "engine_used": f"hybrid_local:{base_engine}+{provider}",
                        "device_used": base_device,
                        "confidence": max(raw_conf, 0.9),
                        "fallback_triggered": fallback_triggered,
                    }
            except Exception as exc:
                logger.warning("hybrid_local VLM refinement failed (%s); returning raw OCR.", exc)
                fallback_triggered = True

        return {
            "text": raw_text,
            "engine_used": base_engine if base_res else "hybrid_local",
            "device_used": base_device,
            "confidence": raw_conf,
            "fallback_triggered": True if provider is None and base_res is not None else fallback_triggered,
        }

    async def extract_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
        engine: Optional[str] = None,
        device: Optional[str] = None,
        lang: Optional[str] = None,
        local_ai_provider: Optional[str] = None,
        local_ai_model: Optional[str] = None,
        local_ai_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extract text/Markdown from image bytes using the requested or auto-selected engine."""
        selected_engine = (
            (engine if engine is not None else getattr(env, "ocr_engine", "auto"))
            or "auto"
        ).strip().lower()

        if selected_engine == "off":
            return {
                "text": "",
                "engine_used": "off",
                "device_used": "none",
                "confidence": 0.0,
                "fallback_triggered": False,
            }

        if not image_bytes:
            return {
                "text": "",
                "engine_used": selected_engine,
                "device_used": "none",
                "confidence": 0.0,
                "fallback_triggered": False,
            }

        if selected_engine == "paddle":
            return await asyncio.to_thread(
                self._extract_paddle_blocking, image_bytes, lang, device
            )

        if selected_engine in ("rapidocr", "onnx"):
            return await asyncio.to_thread(
                self._extract_rapidocr_blocking, image_bytes, device
            )

        if selected_engine == "tesseract":
            return await asyncio.to_thread(
                self._extract_tesseract_blocking, image_bytes, lang
            )

        if selected_engine in LocalVisionParser.SUPPORTED_PROVIDERS or selected_engine == "custom":
            prov = local_ai_provider or selected_engine
            return await self._extract_local_vlm(
                image_bytes=image_bytes,
                mime_type=mime_type,
                provider=prov,
                model=local_ai_model,
                base_url=local_ai_url,
                device=device,
            )

        if selected_engine == "vision":
            return await self._extract_cloud_vision(
                image_bytes=image_bytes, mime_type=mime_type
            )

        if selected_engine == "hybrid_local":
            return await self._run_hybrid_local(
                image_bytes=image_bytes,
                mime_type=mime_type,
                device=device,
                lang=lang,
                local_ai_provider=local_ai_provider,
                local_ai_model=local_ai_model,
                local_ai_url=local_ai_url,
            )

        # Smart "auto" cascade:
        # paddle (GPU if available else CPU) -> rapidocr -> reachable Local VLM -> cloud_vision
        fallback_triggered = False

        try:
            res = await asyncio.to_thread(
                self._extract_paddle_blocking, image_bytes, lang, device
            )
            if res.get("text", "").strip():
                return res
            fallback_triggered = True
        except Exception as exc:
            logger.debug("auto cascade: paddle skipped/failed (%s)", exc)
            fallback_triggered = True

        try:
            res = await asyncio.to_thread(
                self._extract_rapidocr_blocking, image_bytes, device
            )
            if res.get("text", "").strip():
                res["fallback_triggered"] = True
                return res
        except Exception as exc:
            logger.debug("auto cascade: rapidocr skipped/failed (%s)", exc)

        picked = await self._pick_reachable_local_vlm(preferred_provider=local_ai_provider)
        if picked is not None:
            prov, picked_url, picked_model = picked
            try:
                return await self._extract_local_vlm(
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    provider=prov,
                    model=local_ai_model or picked_model,
                    base_url=local_ai_url or picked_url,
                    device=device,
                    fallback_triggered=True,
                )
            except Exception as exc:
                logger.debug("auto cascade: local VLM (%s) failed (%s)", prov, exc)

        try:
            return await self._extract_cloud_vision(
                image_bytes=image_bytes,
                mime_type=mime_type,
                fallback_triggered=True,
            )
        except Exception as exc:
            logger.debug("auto cascade: cloud vision skipped/failed (%s)", exc)

        return {
            "text": "",
            "engine_used": "none",
            "device_used": "none",
            "confidence": 0.0,
            "fallback_triggered": fallback_triggered,
        }

    def extract_image_sync(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
        engine: Optional[str] = None,
        device: Optional[str] = None,
        lang: Optional[str] = None,
        local_ai_provider: Optional[str] = None,
        local_ai_model: Optional[str] = None,
        local_ai_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Synchronous wrapper around :meth:`extract_image`."""
        coro = self.extract_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            engine=engine,
            device=device,
            lang=lang,
            local_ai_provider=local_ai_provider,
            local_ai_model=local_ai_model,
            local_ai_url=local_ai_url,
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    async def extract_batch(
        self,
        images: List[bytes],
        max_concurrency: Optional[int] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Extract OCR results concurrently across a list of images, preserving order."""
        if not images:
            return []
        conc = max(
            1,
            int(
                max_concurrency
                if max_concurrency is not None
                else getattr(env, "ocr_batch_size", 4) or 4
            ),
        )
        sem = asyncio.Semaphore(conc)

        async def _run_one(img_bytes: bytes) -> Dict[str, Any]:
            async with sem:
                return await self.extract_image(img_bytes, **kwargs)

        return list(await asyncio.gather(*(_run_one(img) for img in images)))

    async def get_full_status(self) -> Dict[str, Any]:
        """Return a comprehensive snapshot of hardware, OCR packages, and local AI endpoints."""
        hw = ComputeDeviceManager.detect_hardware()
        selected_engine = getattr(env, "ocr_engine", "auto")
        resolved = ComputeDeviceManager.resolve_device(
            requested=getattr(env, "ocr_device", "auto"),
            engine=selected_engine,
            hardware=hw,
        )
        endpoints = await ComputeDeviceManager.probe_local_ai_endpoints(
            timeout=1.5, transport=self._transport
        )
        return {
            "hardware": hw,
            "resolved_device": resolved,
            "ocr_libraries": {
                "paddleocr": bool(hw.get("paddleocr_available", False)),
                "rapidocr": bool(hw.get("rapidocr_available", False)),
                "tesseract": bool(hw.get("tesseract_available", False)),
                "pytesseract": bool(hw.get("pytesseract_available", False)),
                "onnxruntime": bool(hw.get("onnxruntime_available", False)),
            },
            "local_ai_endpoints": endpoints,
            "config": {
                "ocr_engine": selected_engine,
                "ocr_device": getattr(env, "ocr_device", "auto"),
                "ocr_gpu_id": getattr(env, "ocr_gpu_id", 0),
                "ocr_gpu_mem_mb": getattr(env, "ocr_gpu_mem_mb", 2000),
                "ocr_use_tensorrt": getattr(env, "ocr_use_tensorrt", False),
                "ocr_precision": getattr(env, "ocr_precision", "fp32"),
                "ocr_cpu_threads": getattr(env, "ocr_cpu_threads", 4),
                "ocr_enable_mkldnn": getattr(env, "ocr_enable_mkldnn", "auto"),
                "ocr_batch_size": getattr(env, "ocr_batch_size", 4),
                "ocr_fallback_to_cpu": getattr(env, "ocr_fallback_to_cpu", True),
                "ollama_url": getattr(env, "ollama_url", "http://localhost:11434"),
                "ollama_model": getattr(env, "ollama_model", "llama3.2"),
                "ollama_vision_model": getattr(env, "ollama_vision_model", "qwen2.5vl:7b"),
                "local_ai_base_url": getattr(env, "local_ai_base_url", ""),
                "local_ai_model": getattr(env, "local_ai_model", ""),
            },
        }


__all__ = [
    "ComputeDeviceManager",
    "LocalVisionParser",
    "UnifiedOCREngine",
]
