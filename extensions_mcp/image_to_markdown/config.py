import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # tim .env o cwd cua project


@dataclass(frozen=True)
class Config:
    provider: str            # "gemini" | "openai_compatible"
    api_key: str
    base_url: str | None
    model: str
    default_max_concurrency: int


def load_config() -> Config:
    provider = os.getenv("LLM_PROVIDER", "openai_compatible").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    base_url = (os.getenv("LLM_BASE_URL") or "").strip() or None
    model = os.getenv("LLM_MODEL", "gemini-3.1-pro-preview").strip()
    conc = int(os.getenv("DEFAULT_MAX_CONCURRENCY", "3") or "3")

    if not api_key:
        raise RuntimeError("Thieu LLM_API_KEY trong .env")
    if provider not in {"gemini", "openai_compatible"}:
        raise RuntimeError(
            f"LLM_PROVIDER khong hop le: {provider!r} "
            "(chi nhan 'gemini' hoac 'openai_compatible')"
        )
    if provider == "openai_compatible" and not base_url:
        raise RuntimeError(
            "LLM_PROVIDER=openai_compatible can LLM_BASE_URL. "
            "Hay dien URL proxy noi ban lay key sk-... vao .env (vd https://.../v1)."
        )
    return Config(provider, api_key, base_url, model, max(1, min(conc, 5)))


CONFIG = load_config()
