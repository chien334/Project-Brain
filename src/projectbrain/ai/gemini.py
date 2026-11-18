import httpx
import logging
import os
import re
from typing import List, Dict, Any, Optional
from ..core.config import env
from .adapter import AIAdapter

logger = logging.getLogger(__name__)


def _sanitize_error_message(msg: Any, api_key: Optional[str] = None) -> str:
    text = str(msg or "")
    sanitized = re.sub(r"([?&]key=)[^&\s\"']+", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    if api_key:
        sanitized = sanitized.replace(api_key, "[REDACTED]")
    return sanitized


class GeminiAdapter(AIAdapter):
    def __init__(self, api_key: str = None):
        self.api_key = api_key or env.gemini_key or os.getenv("GEMINI_API_KEY")
        if self.api_key:
            self.api_key = self.api_key.strip().strip('"').strip("'")
        self.base_url = env.gemini_base_url or os.getenv("PB_GEMINI_BASE_URL") or os.getenv("OM_GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta"

    async def chat(self, messages: List[Dict[str, str]], model: str = None, **kwargs) -> str:
        if not self.api_key:
            raise ValueError("Gemini key missing")

        is_openai_compat = self.api_key.startswith("sk-") or (
            env.gemini_base_url and "googleapis.com" not in env.gemini_base_url
        )

        if is_openai_compat:
            # Route to OpenRouter or custom OpenAI-compatible proxy
            m = model or env.gemini_model or "google/gemini-3.1-pro-preview"

            # Map default models to OpenRouter Gemini 3.1 Pro if needed
            if m in ["models/gemini-1.5-flash", "gemini-1.5-flash"]:
                m = "google/gemini-3.1-pro-preview"
            elif m in ["models/gemini-3.1-pro-preview", "gemini-3.1-pro-preview"] and ("openrouter.ai" in self.base_url or not env.gemini_base_url):
                m = "google/gemini-3.1-pro-preview"

            if env.gemini_base_url:
                url = env.gemini_base_url
                if not (url.endswith("/chat/completions") or url.endswith("/completions")):
                    url = url.rstrip("/") + "/chat/completions"
            else:
                url = "https://openrouter.ai/api/v1/chat/completions"

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://projectbrain.org",
                "X-Title": "ProjectBrain Control Panel"
            }

            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    res = await client.post(url, json={
                        "model": m,
                        "messages": messages,
                        **kwargs
                    }, headers=headers)
            except httpx.HTTPError as exc:
                raise Exception(f"OpenAI-compat Gemini Chat Error: {_sanitize_error_message(exc, self.api_key)}") from None

            if res.status_code != 200:
                raise Exception(f"OpenAI-compat Gemini Chat Error: {_sanitize_error_message(res.text, self.api_key)}")

            data = res.json()
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                return ""
        else:
            # Standard Google AI Studio endpoint
            m = model or env.gemini_model or "gemini-1.5-flash"
            if "models/" not in m:
                m = f"models/{m}"

            contents = []
            is_gemma = "gemma" in m.lower()

            for msg in messages:
                role = "user" if msg["role"] == "user" else "model"
                content = msg["content"]
                if msg["role"] == "system":
                    content = f"System Instruction: {content}"
                    role = "user"

                turn = {"parts": [{"text": content}]}
                if not is_gemma:
                    turn["role"] = role
                contents.append(turn)

            url = f"{self.base_url}/{m}:generateContent?key={self.api_key}"

            req_body: Dict[str, Any] = {"contents": contents}
            if not is_gemma and kwargs:
                req_body["generationConfig"] = kwargs

            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    res = await client.post(url, json=req_body)

                    if res.status_code != 200:
                        if m == "models/gemma-4-31b-it" and res.status_code == 500:
                            logger.warning("models/gemma-4-31b-it returned 500. Falling back to models/gemma-4-26b-a4b-it...")
                            fallback_m = "models/gemma-4-26b-a4b-it"
                            fallback_url = f"{self.base_url}/{fallback_m}:generateContent?key={self.api_key}"
                            res = await client.post(fallback_url, json=req_body)
            except httpx.HTTPError as exc:
                raise Exception(f"Gemini Chat Error: {_sanitize_error_message(exc, self.api_key)}") from None

            if res.status_code != 200:
                raise Exception(f"Gemini Chat Error: {_sanitize_error_message(res.text, self.api_key)}")

            data = res.json()
            try:
                parts = data["candidates"][0]["content"]["parts"]
                # Find the first part that does not have thought: true
                for part in parts:
                    if not part.get("thought"):
                        return part.get("text", "")
                return parts[0].get("text", "")
            except (KeyError, IndexError):
                return ""

    async def embed(self, text: str, model: str = None) -> List[float]:
        return (await self.embed_batch([text], model))[0]

    async def embed_batch(self, texts: List[str], model: str = None) -> List[List[float]]:
        if not self.api_key:
            raise ValueError("Gemini key missing")

        is_openai_compat = self.api_key.startswith("sk-") or (
            env.gemini_base_url and "googleapis.com" not in env.gemini_base_url
        )

        if is_openai_compat:
            m = model or "openai/text-embedding-3-small"
            if env.gemini_base_url:
                url = env.gemini_base_url
                if not url.endswith("/embeddings"):
                    url = url.rstrip("/") + "/embeddings"
            else:
                url = "https://openrouter.ai/api/v1/embeddings"

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    res = await client.post(url, json={
                        "model": m,
                        "input": texts
                    }, headers=headers)
            except httpx.HTTPError as exc:
                raise Exception(f"OpenAI-compat Embed Error: {_sanitize_error_message(exc, self.api_key)}") from None

            if res.status_code != 200:
                raise Exception(f"OpenAI-compat Embed Error: {_sanitize_error_message(res.text, self.api_key)}")
            data = res.json()
            return [d["embedding"] for d in data["data"]]
        else:
            # Standard Google AI Studio endpoint
            m = model or env.gemini_embedding_model or "models/gemini-embedding-001"
            if "models/" not in m:
                m = f"models/{m}"

            url = f"{self.base_url}/{m}:batchEmbedContents?key={self.api_key}"

            reqs = []
            for t in texts:
                reqs.append({
                    "model": m,
                    "content": {"parts": [{"text": t}]},
                    "taskType": "SEMANTIC_SIMILARITY"
                })

            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    res = await client.post(url, json={"requests": reqs})
            except httpx.HTTPError as exc:
                raise Exception(f"Gemini: {_sanitize_error_message(exc, self.api_key)}") from None

            if res.status_code != 200:
                raise Exception(f"Gemini: {_sanitize_error_message(res.text, self.api_key)}")

            data = res.json()
            if "embeddings" not in data:
                return []
            return [e["values"] for e in data["embeddings"]]
