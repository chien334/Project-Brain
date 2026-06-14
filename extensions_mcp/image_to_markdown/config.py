import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # tim .env o cwd cua project


@dataclass(frozen=True)
class Config:
    provider: str            # "gemini" | "openai_compatible"
    api_key: str             # co the rong neu chi dung OCR local (PaddleOCR)
    base_url: str | None
    model: str
    default_max_concurrency: int
    ocr_engine: str          # "auto" | "paddle" | "vision" | "off"
    paddle_langs: str        # vd "ch,vi,japan"
    paddle_dpi: int
    paddle_use_gpu: bool


def load_config() -> Config:
    provider = os.getenv("LLM_PROVIDER", "openai_compatible").strip() or "openai_compatible"
    api_key = os.getenv("LLM_API_KEY", "").strip()
    base_url = (os.getenv("LLM_BASE_URL") or "").strip() or None
    model = os.getenv("LLM_MODEL", "gemini-3.1-pro-preview").strip()
    try:
        conc = int(os.getenv("DEFAULT_MAX_CONCURRENCY", "3") or "3")
    except ValueError:
        conc = 3

    ocr_engine = (os.getenv("OCR_ENGINE", "auto").strip().lower() or "auto")
    paddle_langs = os.getenv("OCR_PADDLE_LANG", "ch").strip() or "ch"
    try:
        paddle_dpi = int(os.getenv("OCR_PADDLE_DPI", "200") or "200")
    except ValueError:
        paddle_dpi = 200
    paddle_use_gpu = os.getenv("OCR_PADDLE_USE_GPU", "false").strip().lower() in (
        "1", "true", "yes",
    )

    if provider not in {"gemini", "openai_compatible"}:
        # Khong raise o import-time nua: OCR local (PaddleOCR) khong can vision.
        provider = "openai_compatible"

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
    )


def require_vision() -> Config:
    """Validate cloud-vision config. Goi NGAY TRUOC khi dung vision model.

    Tach rieng khoi load_config() de import module khong fail khi thieu API key
    (truong hop chi dung OCR local bang PaddleOCR).
    """
    if not CONFIG.api_key:
        raise RuntimeError(
            "Thieu LLM_API_KEY: cloud vision OCR can API key. "
            "Dat OCR_ENGINE=paddle de dung OCR local (PaddleOCR), khong can key."
        )
    if CONFIG.provider == "openai_compatible" and not CONFIG.base_url:
        raise RuntimeError(
            "LLM_PROVIDER=openai_compatible can LLM_BASE_URL. "
            "Hay dien URL proxy noi ban lay key sk-... vao .env (vd https://.../v1)."
        )
    return CONFIG


CONFIG = load_config()
