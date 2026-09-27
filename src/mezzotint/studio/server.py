"""Mezzotint Studio — the heavy half, running on the Mac next to Ollama.

The frame (a Raspberry Pi) calls POST /render with a theme and a style. The
studio asks a local LLM to art-direct a specific scene, generates it with a
local image model through Ollama, and returns the PNG plus its title and
the art director's note. Ollama itself stays bound to localhost; only this
service is exposed on the LAN, and only with a shared bearer token.

Run:  mezzotint-studio            (reads settings from the environment)
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import io
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from ..styles import STYLES, get_style
from . import art_director
from .mflux_backend import DEFAULT_MODEL as MFLUX_MODEL
from .mflux_backend import ImageGenError, MfluxImager
from .ollama import Ollama, OllamaError

log = logging.getLogger("mezzotint.studio")


class Settings:
    def __init__(self) -> None:
        self.token = os.environ.get("MEZZOTINT_TOKEN", "")
        self.insecure = os.environ.get("MEZZOTINT_INSECURE") == "1"
        self.ollama_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        self.llm = os.environ.get("MEZZOTINT_LLM", "auto")
        # mflux by default: Ollama disabled image generation in 0.32.6.
        self.image_backend = os.environ.get("MEZZOTINT_IMAGE_BACKEND", "mflux")
        default_model = MFLUX_MODEL if self.image_backend == "mflux" else "x/z-image-turbo"
        self.image_model = os.environ.get("MEZZOTINT_IMAGE_MODEL", default_model)
        self.image_size = os.environ.get("MEZZOTINT_IMAGE_SIZE", "1024x768")
        self.host = os.environ.get("MEZZOTINT_HOST", "0.0.0.0")
        self.port = int(os.environ.get("MEZZOTINT_PORT", "8765"))


class RenderRequest(BaseModel):
    theme: str = Field("", max_length=300)
    style_id: str = "riso"
    recent_titles: list[str] = Field(default_factory=list, max_length=50)
    scene: str | None = Field(None, max_length=400, description="skip the art director and use this scene")


class RenderResponse(BaseModel):
    title: str
    scene: str
    note: str
    prompt: str
    style_id: str
    director: str
    image_model: str
    image_png_b64: str
    width: int
    height: int
    seconds: dict


def create_app(settings: Settings | None = None, ollama: Ollama | None = None,
               imager: MfluxImager | None = None) -> FastAPI:
    settings = settings or Settings()
    if not settings.token and not settings.insecure:
        raise SystemExit("MEZZOTINT_TOKEN is not set. Run deploy/mac/install.sh or export a token.")

    state: dict = {"ollama": ollama, "llm": None, "lock": asyncio.Lock(), "last": None}
    if settings.image_backend == "mflux":
        imager = imager or MfluxImager(settings.image_model)

    async def draw(prompt: str) -> bytes:
        if settings.image_backend == "mflux":
            return await imager.generate(prompt, settings.image_size)
        return await state["ollama"].generate_image(settings.image_model, prompt, settings.image_size)

    async def image_ready(up: bool) -> bool:
        if settings.image_backend == "mflux":
            return imager.available()
        return await state["ollama"].has_model(settings.image_model) if up else False

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["ollama"] is None:
            state["ollama"] = Ollama(settings.ollama_url)
        state["llm"] = await state["ollama"].pick_llm(settings.llm)
        log.info("studio up: llm=%s image=%s via %s", state["llm"], settings.image_model, settings.image_backend)
        yield
        await state["ollama"].close()

    app = FastAPI(title="Mezzotint Studio", version="1.0", lifespan=lifespan)

    def auth(authorization: str = Header(default="")) -> None:
        if settings.insecure and not settings.token:
            return
        supplied = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), settings.token.encode()):
            raise HTTPException(401, "bad token")

    @app.get("/health")
    async def health(_: None = Depends(auth)):
        ol: Ollama = state["ollama"]
        up = await ol.reachable()
        if up and state["llm"] is None:
            state["llm"] = await ol.pick_llm(settings.llm)
        return {
            "ok": up,
            "ollama": up,
            "llm": state["llm"],
            "image_model": settings.image_model,
            "image_backend": settings.image_backend,
            "image_model_installed": await image_ready(up),
            "busy": state["lock"].locked(),
            "last": state["last"],
        }

    @app.get("/styles")
    async def styles(_: None = Depends(auth)):
        return [s.public() for s in STYLES]

    @app.post("/render", response_model=RenderResponse)
    async def render(req: RenderRequest, _: None = Depends(auth)):
        ol: Ollama = state["ollama"]
        style = get_style(req.style_id)
        async with state["lock"]:  # one GPU, one job at a time
            t0 = time.monotonic()
            if req.scene:
                comp = {"title": "Commission", "scene": req.scene, "note": "Scene supplied directly.", "director": "you"}
            else:
                comp = await art_director.direct(ol, state["llm"], req.theme, style, req.recent_titles)
            t1 = time.monotonic()
            prompt = art_director.build_image_prompt(comp["scene"], style)
            try:
                png = await draw(prompt)
            except (OllamaError, ImageGenError) as e:
                raise HTTPException(502, str(e)) from e
            t2 = time.monotonic()

        from PIL import Image  # local import keeps startup quick

        with Image.open(io.BytesIO(png)) as im:
            w, h = im.size
        state["last"] = {"title": comp["title"], "at": time.time(), "seconds": round(t2 - t0, 1)}
        log.info("rendered %r in %.1fs (direction %.1fs)", comp["title"], t2 - t0, t1 - t0)
        return RenderResponse(
            title=comp["title"],
            scene=comp["scene"],
            note=comp.get("note", ""),
            prompt=prompt,
            style_id=style.id,
            director=comp.get("director", ""),
            image_model=settings.image_model,
            image_png_b64=base64.b64encode(png).decode(),
            width=w,
            height=h,
            seconds={"direction": round(t1 - t0, 2), "image": round(t2 - t1, 2)},
        )

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s = Settings()
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    main()
