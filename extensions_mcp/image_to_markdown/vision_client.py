import base64

from .config import CONFIG, require_vision

DEFAULT_PROMPT = (
    "You are an OCR + document-structuring engine.\n"
    "Extract ALL text from the image and output clean GitHub-Flavored Markdown.\n"
    "Preserve structure: headings, lists, tables, code blocks, and reading order.\n"
    "Do NOT translate. Do NOT add commentary. If there is no text, output an empty string.\n"
    "Output ONLY the Markdown."
)

# Client duoc khoi tao LAZY (lan dau dung), de import module khong fail khi
# thieu API key — cho phep OCR local (PaddleOCR) chay ma khong can cloud key.
_gemini = None
_openai = None


def _get_client():
    """Tra ve client vision da khoi tao, validate config truoc."""
    global _gemini, _openai
    require_vision()
    if CONFIG.provider == "gemini":
        if _gemini is None:
            from google import genai

            _gemini = genai.Client(api_key=CONFIG.api_key)
        return _gemini
    if _openai is None:
        from openai import AsyncOpenAI

        _openai = AsyncOpenAI(api_key=CONFIG.api_key, base_url=CONFIG.base_url)
    return _openai


async def extract_text_from_image(
    image_bytes: bytes, mime_type: str, prompt: str, model: str
) -> str:
    """Goi vision model trich text tu 1 anh, tra ve Markdown."""
    client = _get_client()
    if CONFIG.provider == "gemini":
        from google.genai import types as genai_types

        resp = await client.aio.models.generate_content(
            model=model,
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
        )
        return (resp.text or "").strip()
    else:
        b64 = base64.b64encode(image_bytes).decode()
        resp = await client.chat.completions.create(
            model=model,
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
