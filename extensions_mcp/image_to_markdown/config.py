import os
from dataclasses import dataclass, replace
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()  # tim .env o cwd cua project

SUPPORTED_PROVIDERS = {
    "gemini",
    "openai_compatible",
    "ollama",
    "vllm",
    "lmstudio",
    "llamacpp",
}
LOCAL_AI_PROVIDERS = {"ollama", "vllm", "lmstudio", "llamacpp"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal", "::1"}

DEFAULT_LOCAL_URLS = {
    "ollama": "http://localhost:11434",
    "vllm": "http://localhost:8000/v1",
    "lmstudio": "http://localhost:1234/v1",
    "llamacpp": "http://localhost:8080/v1",
}


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _parse_optional_bool(raw: str | None) -> bool | None:
    if raw is None or not raw.strip() or raw.strip().lower() == "auto":
        return None
    val = raw.strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return None


def _parse_int(raw: str | None, default: int, minimum: int = 0) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw.strip()))
    except ValueError:
        return default


def is_local_base_url(base_url: str | None) -> bool:
    """Return True if base_url targets localhost / 127.0.0.1 / 0.0.0.0 / host.docker.internal."""
    if not base_url:
        return False
    try:
        parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
        host = (parsed.hostname or "").strip().lower()
        return host in _LOCAL_HOSTS
    except Exception:
        return False


def is_local_endpoint(provider: str, base_url: str | None) -> bool:
    """Return True when the provider is a keyless local AI server or points to a local host."""
    return (provider or "").strip().lower() in LOCAL_AI_PROVIDERS or is_local_base_url(base_url)


def resolve_base_url(provider: str, base_url: str | None = None) -> str | None:
    """Resolve effective base_url for a provider, applying local defaults when unset."""
    if base_url and base_url.strip():
        return base_url.strip()
    p = (provider or "").strip().lower()
    env_overrides = {
        "ollama": os.getenv("OLLAMA_BASE_URL") or os.getenv("LOCAL_AI_URL"),
        "vllm": os.getenv("VLLM_BASE_URL") or os.getenv("LOCAL_AI_URL"),
        "lmstudio": os.getenv("LMSTUDIO_BASE_URL") or os.getenv("LOCAL_AI_URL"),
        "llamacpp": os.getenv("LLAMACPP_BASE_URL") or os.getenv("LOCAL_AI_URL"),
    }
    if p in env_overrides and env_overrides[p] and env_overrides[p].strip():
        return env_overrides[p].strip()
    return DEFAULT_LOCAL_URLS.get(p)


@dataclass(frozen=True)
class Config:
    provider: str            # "gemini" | "openai_compatible" | "ollama" | "vllm" | "lmstudio" | "llamacpp"
    api_key: str             # co the rong neu dung OCR local hoac Local AI (ollama/vllm/lmstudio/llamacpp)
    base_url: str | None
    model: str
    default_max_concurrency: int
    ocr_engine: str          # "auto" | "paddle" | "rapidocr" | "tesseract" | "vision" | "ollama" | "vllm" | "lmstudio" | "llamacpp" | "hybrid_local" | "off"
    paddle_langs: str        # vd "ch,vi,japan"
    paddle_dpi: int
    paddle_use_gpu: bool
    ocr_device: str = "auto"
    ocr_cpu_threads: int = 4
    ocr_enable_mkldnn: bool | None = None
    ocr_gpu_id: int = 0
    ocr_gpu_mem_mb: int = 500
    ocr_fallback_to_cpu: bool = True


def load_config() -> Config:
    provider = (os.getenv("LLM_PROVIDER", "openai_compatible").strip().lower() or "openai_compatible")
    if provider not in SUPPORTED_PROVIDERS:
        # Khong raise o import-time: OCR local (PaddleOCR) khong can vision.
        provider = "openai_compatible"

    api_key = os.getenv("LLM_API_KEY", "").strip()
    raw_base_url = (os.getenv("LLM_BASE_URL") or "").strip() or None
    base_url = resolve_base_url(provider, raw_base_url)

    model = os.getenv("LLM_MODEL", "").strip()
    if not model:
        if provider in LOCAL_AI_PROVIDERS:
            model = os.getenv("LOCAL_AI_MODEL", "").strip() or (
                "qwen2.5vl" if provider == "ollama" else "default"
            )
        else:
            model = "gemini-3.1-pro-preview"

    conc = _parse_int(os.getenv("DEFAULT_MAX_CONCURRENCY"), 3, minimum=1)

    ocr_engine = (os.getenv("OCR_ENGINE", "auto").strip().lower() or "auto")
    paddle_langs = os.getenv("OCR_PADDLE_LANG", "ch").strip() or "ch"
    paddle_dpi = _parse_int(os.getenv("OCR_PADDLE_DPI"), 200, minimum=72)
    paddle_use_gpu = _parse_bool(os.getenv("OCR_PADDLE_USE_GPU"), False)

    raw_device = (os.getenv("PB_OCR_DEVICE") or os.getenv("OCR_DEVICE") or "").strip().lower()
    if raw_device:
        ocr_device = raw_device
    elif paddle_use_gpu:
        ocr_device = "gpu"
    else:
        ocr_device = "auto"

    default_threads = max(1, min(os.cpu_count() or 4, 8))
    ocr_cpu_threads = _parse_int(
        os.getenv("PB_OCR_CPU_THREADS") or os.getenv("OCR_CPU_THREADS"),
        default_threads,
        minimum=1,
    )
    ocr_enable_mkldnn = _parse_optional_bool(
        os.getenv("PB_OCR_ENABLE_MKLDNN") or os.getenv("OCR_ENABLE_MKLDNN")
    )
    ocr_gpu_id = _parse_int(os.getenv("PB_OCR_GPU_ID") or os.getenv("OCR_GPU_ID"), 0, minimum=0)
    ocr_gpu_mem_mb = _parse_int(
        os.getenv("PB_OCR_GPU_MEM_MB") or os.getenv("OCR_GPU_MEM_MB"), 500, minimum=64
    )
    ocr_fallback_to_cpu = _parse_bool(
        os.getenv("PB_OCR_FALLBACK_TO_CPU") or os.getenv("OCR_FALLBACK_TO_CPU"), True
    )

    if is_local_endpoint(provider, base_url) and not api_key:
        api_key = "local-no-key"

    return Config(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        default_max_concurrency=max(1, min(conc, 5)),
        ocr_engine=ocr_engine,
        paddle_langs=paddle_langs,
        paddle_dpi=paddle_dpi,
        paddle_use_gpu=paddle_use_gpu,
        ocr_device=ocr_device,
        ocr_cpu_threads=ocr_cpu_threads,
        ocr_enable_mkldnn=ocr_enable_mkldnn,
        ocr_gpu_id=ocr_gpu_id,
        ocr_gpu_mem_mb=ocr_gpu_mem_mb,
        ocr_fallback_to_cpu=ocr_fallback_to_cpu,
    )


def effective_api_key(cfg: Config | None = None, provider: str | None = None, base_url: str | None = None) -> str:
    """Return configured API key, supplying 'local-no-key' for keyless local AI endpoints."""
    c = cfg or CONFIG
    eff_provider = (provider or c.provider or "").strip().lower()
    eff_url = base_url if base_url is not None else c.base_url
    if c.api_key:
        return c.api_key
    if is_local_endpoint(eff_provider, eff_url):
        return "local-no-key"
    return ""


def require_vision() -> Config:
    """Validate vision config. Goi NGAY TRUOC khi dung vision model.

    Khong fail neu la local AI provider (ollama/vllm/lmstudio/llamacpp) hoac
    base_url tro toi localhost/127.0.0.1/0.0.0.0/host.docker.internal.
    """
    eff_provider = (CONFIG.provider or "").strip().lower()
    eff_url = resolve_base_url(eff_provider, CONFIG.base_url)
    local = is_local_endpoint(eff_provider, eff_url)

    if eff_provider == "openai_compatible" and not eff_url:
        raise RuntimeError(
            "LLM_PROVIDER=openai_compatible can LLM_BASE_URL. "
            "Hay dien URL proxy noi ban lay key sk-... vao .env (vd https://.../v1)."
        )

    if not CONFIG.api_key and not local:
        raise RuntimeError(
            "Thieu LLM_API_KEY: cloud vision OCR can API key. "
            "Dat OCR_ENGINE=paddle de dung OCR local (PaddleOCR), hoac dung "
            "LLM_PROVIDER=ollama|vllm|lmstudio|llamacpp de chay khong can key."
        )

    if not CONFIG.api_key and local:
        return replace(CONFIG, api_key="local-no-key", base_url=eff_url)
    if eff_url != CONFIG.base_url:
        return replace(CONFIG, base_url=eff_url)
    return CONFIG


CONFIG = load_config()
