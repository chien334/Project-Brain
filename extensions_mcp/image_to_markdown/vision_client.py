import base64
import inspect
import os

from .config import (
    CONFIG,
    LOCAL_AI_PROVIDERS,
    effective_api_key,
    is_local_endpoint,
    require_vision,
    resolve_base_url,
)

DEFAULT_PROMPT = (
    "You are an OCR + document-structuring engine.\n"
    "Extract ALL text from the image and output clean GitHub-Flavored Markdown.\n"
    "Preserve structure: headings, lists, tables, code blocks, and reading order.\n"
    "Do NOT translate. Do NOT add commentary. If there is no text, output an empty string.\n"
    "Output ONLY the Markdown."
)

# Client duoc khoi tao LAZY (lan dau dung), de import module khong fail khi
# thieu API key — cho phep OCR local (PaddleOCR / Local AI) chay ma khong can cloud key.
_gemini = None
_openai = None


def _get_client():
    """Tra ve client vision da khoi tao cho CONFIG mac dinh, validate config truoc."""
    global _gemini, _openai
    cfg = require_vision()
    if cfg.provider == "gemini":
        if _gemini is None:
            from google import genai

            _gemini = genai.Client(api_key=cfg.api_key)
        return _gemini
    if _openai is None:
        from openai import AsyncOpenAI

        api_key = cfg.api_key or effective_api_key(cfg)
        _openai = AsyncOpenAI(api_key=api_key, base_url=cfg.base_url)
    return _openai


def _normalize_ollama_native_base(base_url: str | None) -> str:
    base = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base or "http://localhost:11434"


async def _try_local_vision_parser(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    model: str,
    provider: str,
    base_url: str | None,
    api_key: str | None = None,
    timeout: float = 120.0,
) -> str | None:
    """Try delegating to LocalVisionParser from projectbrain.ocr_runtime if available."""
    try:
        from projectbrain.ocr_runtime import LocalVisionParser  # type: ignore
    except Exception:
        return None

    try:
        parser = LocalVisionParser(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )
        res = await parser.parse_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=prompt,
            model=model,
        )
        if isinstance(res, str) and res.strip():
            return res.strip()
    except Exception:
        return None
    return None


async def _extract_via_ollama_native(
    image_bytes: bytes,
    prompt: str,
    model: str,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> str:
    """Call Ollama native /api/chat with base64-encoded image."""
    import httpx

    eff_base = _normalize_ollama_native_base(base_url)
    eff_model = model or os.getenv("LOCAL_AI_MODEL") or os.getenv("OLLAMA_MODEL") or "qwen2.5vl"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": eff_model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [b64],
            }
        ],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{eff_base}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    msg = data.get("message") if isinstance(data, dict) else None
    if isinstance(msg, dict):
        return (msg.get("content") or "").strip()
    return str(data.get("response") or "").strip() if isinstance(data, dict) else ""


async def extract_text_from_image(
    image_bytes: bytes,
    mime_type: str,
    prompt: str = DEFAULT_PROMPT,
    model: str | None = None,
    *,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = 120.0,
) -> str:
    """Goi vision model (Gemini, OpenAI-compat, Ollama, vLLM, LM Studio, llama.cpp) trich text -> Markdown."""
    eff_provider = (provider or CONFIG.provider or "openai_compatible").strip().lower()
    eff_model = (model or CONFIG.model or "").strip()
    if not eff_model:
        eff_model = "qwen2.5vl" if eff_provider == "ollama" else "gemini-3.1-pro-preview"

    eff_base = base_url
    if eff_base is None:
        if provider is None or eff_provider == CONFIG.provider:
            eff_base = resolve_base_url(eff_provider, CONFIG.base_url)
        else:
            eff_base = resolve_base_url(eff_provider, None)

    if eff_provider in LOCAL_AI_PROVIDERS:
        delegated = await _try_local_vision_parser(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=prompt,
            model=eff_model,
            provider=eff_provider,
            base_url=eff_base,
        )
        if delegated:
            return delegated

    if eff_provider == "ollama":
        return await _extract_via_ollama_native(
            image_bytes=image_bytes,
            prompt=prompt,
            model=eff_model,
            base_url=eff_base,
            timeout=timeout,
        )

    if eff_provider == "gemini":
        if provider is None and api_key is None:
            client = _get_client()
        else:
            eff_key = api_key or CONFIG.api_key
            if not eff_key:
                require_vision()
            from google import genai

            client = genai.Client(api_key=eff_key)

        from google.genai import types as genai_types

        resp = await client.aio.models.generate_content(
            model=eff_model,
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
        )
        return (resp.text or "").strip()

    # OpenAI-compatible (including vllm / lmstudio / llamacpp keyless local endpoints)
    use_default_client = (
        provider is None
        and base_url is None
        and api_key is None
        and CONFIG.provider != "gemini"
    )
    if use_default_client:
        client = _get_client()
    else:
        from openai import AsyncOpenAI

        eff_key = api_key or effective_api_key(CONFIG, eff_provider, eff_base)
        if not eff_base and eff_provider == "openai_compatible":
            raise RuntimeError(
                "LLM_PROVIDER=openai_compatible can LLM_BASE_URL."
            )
        if not eff_key and not is_local_endpoint(eff_provider, eff_base):
            require_vision()
        client = AsyncOpenAI(
            api_key=eff_key or "local-no-key",
            base_url=eff_base,
            timeout=timeout,
        )

    b64 = base64.b64encode(image_bytes).decode()
    resp = await client.chat.completions.create(
        model=eff_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                    },
                ],
            }
        ],
    )
    return (resp.choices[0].message.content or "").strip()
