import base64

from .config import CONFIG

DEFAULT_PROMPT = (
    "You are an OCR + document-structuring engine.\n"
    "Extract ALL text from the image and output clean GitHub-Flavored Markdown.\n"
    "Preserve structure: headings, lists, tables, code blocks, and reading order.\n"
    "Do NOT translate. Do NOT add commentary. If there is no text, output an empty string.\n"
    "Output ONLY the Markdown."
)

# --- Khoi tao client mot lan ---
if CONFIG.provider == "gemini":
    from google import genai
    from google.genai import types as genai_types

    _gemini = genai.Client(api_key=CONFIG.api_key)
else:
    from openai import AsyncOpenAI

    _openai = AsyncOpenAI(api_key=CONFIG.api_key, base_url=CONFIG.base_url)


async def extract_text_from_image(
    image_bytes: bytes, mime_type: str, prompt: str, model: str
) -> str:
    """Goi vision model trich text tu 1 anh, tra ve Markdown."""
    if CONFIG.provider == "gemini":
        resp = await _gemini.aio.models.generate_content(
            model=model,
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
        )
        return (resp.text or "").strip()
    else:
        b64 = base64.b64encode(image_bytes).decode()
        resp = await _openai.chat.completions.create(
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
