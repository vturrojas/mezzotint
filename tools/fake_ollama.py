"""A stand-in for Ollama, for developing Mezzotint without a GPU.

Serves just enough of the API (/api/tags, /api/chat, /v1/images/generations)
for the studio: the "LLM" invents a title and scene, the "image model" draws a
random two-ink landscape. Run it, then point the studio at it:

    python tools/fake_ollama.py            # listens on 127.0.0.1:11999
    OLLAMA_URL=http://127.0.0.1:11999 MEZZOTINT_INSECURE=1 mezzotint-studio
"""

from __future__ import annotations

import base64
import io
import json
import random

from fastapi import FastAPI, Request
from PIL import Image, ImageDraw, ImageFilter

app = FastAPI()
NOUNS = ["Heron", "Lighthouse", "Orchard", "Harbour", "Comet", "Tramline", "Fox", "Cypress", "Kite", "Signal Tower"]
MOODS = ["at Low Tide", "Before Rain", "in Late Light", "After the Storm", "at First Frost", "Alone", "Waiting"]


@app.get("/api/tags")
async def tags():
    return {"models": [
        {"name": "qwen3:8b", "size": 5_000_000_000, "capabilities": ["completion"]},
        {"name": "x/z-image-turbo:latest", "size": 12_000_000_000, "capabilities": ["image"]},
    ]}


@app.post("/api/chat")
async def chat(req: Request):
    body = await req.json()
    user = body["messages"][-1]["content"]
    theme = user.split("\n")[0].replace("THEME:", "").strip()
    title = f"{random.choice(NOUNS)} {random.choice(MOODS)}"
    content = json.dumps({
        "title": title,
        "scene": f"A lone {title.lower()} for the theme '{theme}', large silhouette on the right third, low red sun, wide empty sky",
        "note": "One big shape and one red note; it will read from across the room.",
    })
    return {"message": {"role": "assistant", "content": content}, "done": True}


def _draw(w: int, h: int) -> bytes:
    rng = random.Random()
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    top = (rng.randint(200, 250), rng.randint(180, 235), rng.randint(150, 220))
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(c * (1 - 0.35 * t)) for c in top))
    r = rng.randint(h // 10, h // 5)
    cx, cy = rng.randint(r, w - r), rng.randint(r, h // 2)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(225, rng.randint(40, 70), 30))
    for layer, shade in enumerate((110, 60, 28)):
        base = h * (0.45 + 0.15 * layer)
        pts = [(0, h)]
        for x in range(0, w + 64, 64):
            pts.append((x, base + rng.randint(-h // 8, h // 10)))
        pts.append((w, h))
        d.polygon(pts, fill=(shade, shade, shade + 20))
    bx = rng.randint(w // 5, 4 * w // 5)
    d.polygon([(bx, h * .92), (bx + w * .04, h * .72), (bx + w * .04, h * .92)], fill=(210, 60, 45))
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


@app.post("/api/generate")
async def generate(req: Request):
    body = await req.json()
    return {"image": base64.b64encode(_draw(int(body.get("width", 1024)), int(body.get("height", 768)))).decode(), "done": True}


@app.post("/v1/images/generations")
async def images(req: Request):
    body = await req.json()
    w, h = (int(v) for v in body.get("size", "1024x768").split("x"))
    return {"created": 0, "data": [{"b64_json": base64.b64encode(_draw(w, h)).decode()}]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=11999, log_level="warning")
