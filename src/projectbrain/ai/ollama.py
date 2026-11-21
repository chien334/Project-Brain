import asyncio
import base64
from typing import Any, Dict, List, Optional

import httpx

from ..core.config import env
from .adapter import AIAdapter

_VISION_MODEL_HINTS = (
    "vl",
    "llava",
    "minicpm-v",
    "gemma3",
    "vision",
    "moondream",
    "bakllava",
    "pixtral",
    "internvl",
)


class OllamaAdapter(AIAdapter):
    """Adapter for local or remote Ollama servers (chat, vision_chat, embeddings, health)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        base = getattr(env, "ollama_base_url", None) or getattr(env, "ollama_url", None)
        self.base_url = (base_url or base or "http://localhost:11434").rstrip("/")
        self.timeout = float(timeout)
        self.transport = transport

    def _client(self, timeout: Optional[float] = None) -> httpx.AsyncClient:
        kwargs: Dict[str, Any] = {
            "timeout": self.timeout if timeout is None else float(timeout)
        }
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        **kwargs,
    ) -> str:
        m = model or getattr(env, "ollama_model", None) or "llama3.2"
        url = f"{self.base_url}/api/chat"
        async with self._client() as client:
            res = await client.post(
                url,
                json={
                    "model": m,
                    "messages": messages,
                    "stream": False,
                    **kwargs,
                },
            )
            if res.status_code != 200:
                raise Exception(f"Ollama: {res.text}")
            data = res.json()
            return (data.get("message") or {}).get("content", "")

    async def vision_chat(
        self,
        prompt: str,
        image_bytes_list: List[bytes],
        model: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Send one or more raw images (base64-encoded) with a prompt to Ollama /api/chat."""
        m = model or getattr(env, "ollama_vision_model", None) or "qwen2.5vl:7b"
        encoded_images = [
            base64.b64encode(img).decode("ascii")
            for img in (image_bytes_list or [])
            if img
        ]
        message: Dict[str, Any] = {
            "role": "user",
            "content": prompt,
        }
        if encoded_images:
            message["images"] = encoded_images
        return await self.chat([message], model=m, **kwargs)

    async def check_health_and_models(self, timeout: float = 2.0) -> Dict[str, Any]:
        """Probe Ollama /api/tags and return reachability plus available/vision models."""
        url = f"{self.base_url}/api/tags"
        try:
            async with self._client(timeout=timeout) as client:
                res = await client.get(url)
                if res.status_code != 200:
                    return {
                        "reachable": False,
                        "base_url": self.base_url,
                        "models": [],
                        "vision_models": [],
                        "error": f"HTTP {res.status_code}: {res.text[:200]}",
                    }
                data = res.json() or {}
                raw_models = data.get("models") or []
                models = [
                    str(item["name"])
                    for item in raw_models
                    if isinstance(item, dict) and item.get("name")
                ]
                vision_models = [
                    name
                    for name in models
                    if any(hint in name.lower() for hint in _VISION_MODEL_HINTS)
                ]
                return {
                    "reachable": True,
                    "base_url": self.base_url,
                    "models": models,
                    "vision_models": vision_models,
                    "error": None,
                }
        except Exception as exc:
            return {
                "reachable": False,
                "base_url": self.base_url,
                "models": [],
                "vision_models": [],
                "error": str(exc),
            }

    async def embed(self, text: str, model: Optional[str] = None) -> List[float]:
        m = model or getattr(env, "ollama_embedding_model", None) or "nomic-embed-text"
        return (await self.embed_batch([text], m))[0]

    async def embed_batch(
        self,
        texts: List[str],
        model: Optional[str] = None,
        max_concurrency: int = 4,
    ) -> List[List[float]]:
        if not texts:
            return []
        m = model or getattr(env, "ollama_embedding_model", None) or "nomic-embed-text"
        url = f"{self.base_url}/api/embeddings"
        sem = asyncio.Semaphore(max(1, int(max_concurrency)))

        async with self._client() as client:
            async def _embed_one(text: str) -> List[float]:
                async with sem:
                    r = await client.post(url, json={"model": m, "prompt": text})
                    if r.status_code != 200:
                        raise Exception(f"Ollama Emb: {r.text}")
                    return r.json()["embedding"]

            return list(await asyncio.gather(*(_embed_one(t) for t in texts)))
