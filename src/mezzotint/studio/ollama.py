"""Thin async client for the parts of the Ollama API the studio needs."""

from __future__ import annotations

import base64
import json
import logging

import httpx

log = logging.getLogger("mezzotint.studio.ollama")

# Families that make good, fast art directors, best first.
_LLM_PREFERENCE = ("qwen3", "gemma3", "gemma", "llama3", "mistral", "phi", "granite", "gpt-oss")


class OllamaError(RuntimeError):
    pass


class Ollama:
    def __init__(self, base_url: str, timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        # trust_env=False: Ollama is local, so never route it through an HTTP proxy
        # that happens to be set in the environment (curl and httpx would both try).
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, trust_env=False)

    async def close(self) -> None:
        await self.client.aclose()

    async def models(self) -> list[dict]:
        r = await self.client.get("/api/tags", timeout=10)
        r.raise_for_status()
        return r.json().get("models", [])

    async def reachable(self) -> bool:
        try:
            await self.models()
            return True
        except Exception:
            return False

    @staticmethod
    def _is_image_model(m: dict) -> bool:
        caps = m.get("capabilities") or []
        name = m.get("name", "")
        return "image" in caps or name.startswith("x/") or "image" in name or "flux" in name

    @staticmethod
    def _is_text_model(m: dict) -> bool:
        caps = m.get("capabilities") or []
        name = m.get("name", "").lower()
        if caps:
            return "completion" in caps and "image" not in caps and "embedding" not in caps
        return not Ollama._is_image_model(m) and "embed" not in name

    async def pick_llm(self, wanted: str) -> str | None:
        """Honour an explicit model name; with 'auto', choose the best installed one."""
        try:
            installed = await self.models()
        except Exception:
            return None
        names = [m["name"] for m in installed]
        if wanted and wanted != "auto":
            for n in names:
                if n == wanted or n.split(":")[0] == wanted:
                    return n
            log.warning("LLM %s not installed; falling back to auto", wanted)
        text = [m for m in installed if self._is_text_model(m)]
        for fam in _LLM_PREFERENCE:
            for m in sorted(text, key=lambda m: m.get("size", 0)):
                if m["name"].lower().startswith(fam):
                    return m["name"]
        return text[0]["name"] if text else None

    async def has_model(self, name: str) -> bool:
        try:
            return any(m["name"] == name or m["name"] == f"{name}:latest" for m in await self.models())
        except Exception:
            return False

    async def chat_json(self, model: str, system: str, user: str, schema: dict, temperature: float = 0.9) -> dict:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "format": schema,
            "think": False,
            "keep_alive": "2m",  # free the Mac's memory soon after directing
            "options": {"temperature": temperature},
        }
        r = await self.client.post("/api/chat", json=body, timeout=180)
        if r.status_code == 400 and "think" in r.text.lower():
            body.pop("think")  # models without a thinking switch reject the field
            r = await self.client.post("/api/chat", json=body, timeout=180)
        if r.status_code != 200:
            raise OllamaError(f"chat failed ({r.status_code}): {r.text[:300]}")
        content = r.json().get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise OllamaError(f"art director returned non-JSON: {content[:200]}") from e

    async def generate_image(self, model: str, prompt: str, size: str) -> bytes:
        """Text-to-image. Uses Ollama's native /api/generate (width/height at the
        top level, base64 PNG back in "image"); falls back to the OpenAI-style
        /v1/images/generations route on servers that only expose that one."""
        w, h = (int(v) for v in size.lower().split("x"))
        body = {"model": model, "prompt": prompt, "width": w, "height": h, "stream": False}
        r = await self.client.post("/api/generate", json=body)
        if r.status_code == 200:
            data = r.json()
            b64 = data.get("image") or (data.get("images") or [None])[0]
            if not b64:
                raise OllamaError(f"{model} returned text instead of an image; is it an image model?")
            return _decode(b64)
        if r.status_code != 404:
            raise OllamaError(f"image generation failed ({r.status_code}): {r.text[:300]}")

        body = {"model": model, "prompt": prompt, "size": size, "response_format": "b64_json", "n": 1}
        r = await self.client.post("/v1/images/generations", json=body)
        if r.status_code != 200:
            raise OllamaError(f"image generation failed ({r.status_code}): {r.text[:300]}")
        data = r.json().get("data") or []
        if not data or not data[0].get("b64_json"):
            raise OllamaError("image generation returned no image")
        return _decode(data[0]["b64_json"])


def _decode(b64: str) -> bytes:
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    return base64.b64decode(b64)
