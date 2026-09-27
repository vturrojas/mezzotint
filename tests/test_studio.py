import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mezzotint.studio import art_director
from mezzotint.studio.ollama import Ollama
from mezzotint.studio.server import Settings, create_app
from mezzotint.styles import get_style


def _png(w=1024, h=768):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 80, 60)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def fake_ollama(chat_content='{"title":"Red Kite","scene":"a red kite over black dunes at noon","note":"one accent"}',
                models=None, image_status=200, native=True):
    seen = {}

    def handler(req: httpx.Request):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": models if models is not None else [
                {"name": "nomic-embed-text:latest", "capabilities": ["embedding"]},
                {"name": "gemma3:4b", "size": 3, "capabilities": ["completion"]},
                {"name": "qwen3:8b", "size": 5, "capabilities": ["completion"]},
                {"name": "x/z-image-turbo:latest", "capabilities": ["image"]},
            ]})
        if req.url.path == "/api/chat":
            seen["chat"] = json.loads(req.content)
            return httpx.Response(200, json={"message": {"content": chat_content}})
        if req.url.path == "/api/generate":
            seen["image"] = json.loads(req.content)
            if not native:
                return httpx.Response(404, text="404 page not found")
            if image_status != 200:
                return httpx.Response(image_status, text="model not found")
            return httpx.Response(200, json={"image": _png(), "done": True})
        if req.url.path == "/v1/images/generations":
            seen["image"] = json.loads(req.content)
            if image_status != 200:
                return httpx.Response(image_status, text="model not found")
            return httpx.Response(200, json={"data": [{"b64_json": _png()}]})
        return httpx.Response(404)

    ol = Ollama("http://ollama.test")
    ol.client = httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return ol, seen


def settings(**kw):
    s = Settings()
    s.token = "t0ken"
    s.insecure = False
    s.image_backend = "ollama"
    s.image_model = "x/z-image-turbo"
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_requires_token():
    ol, _ = fake_ollama()
    with TestClient(create_app(settings(), ol)) as c:
        assert c.get("/health").status_code == 401
        assert c.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/health", headers={"Authorization": "Bearer t0ken"}).json()["ok"] is True


def test_refuses_to_start_without_token():
    s = settings(token="")
    with pytest.raises(SystemExit):
        create_app(s, fake_ollama()[0])


def test_auto_picks_preferred_text_model():
    ol, _ = fake_ollama()
    with TestClient(create_app(settings(), ol)) as c:
        h = c.get("/health", headers={"Authorization": "Bearer t0ken"}).json()
    assert h["llm"] == "qwen3:8b" and h["image_model_installed"] is True


def test_render_happy_path_carries_style_into_prompt():
    ol, seen = fake_ollama()
    with TestClient(create_app(settings(), ol)) as c:
        r = c.post("/render", json={"theme": "birds", "style_id": "linocut", "recent_titles": ["Old One"]},
                   headers={"Authorization": "Bearer t0ken"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Red Kite" and body["director"] == "qwen3:8b"
    assert "linocut" in body["prompt"].lower() and "vermilion" in body["prompt"].lower()
    assert (body["width"], body["height"]) == (1024, 768)
    assert (seen["image"]["width"], seen["image"]["height"]) == (1024, 768)
    assert "Old One" in seen["chat"]["messages"][-1]["content"]


def test_bad_llm_output_falls_back_gracefully():
    ol, _ = fake_ollama(chat_content="I am not JSON")
    with TestClient(create_app(settings(), ol)) as c:
        r = c.post("/render", json={"theme": "tide pools"}, headers={"Authorization": "Bearer t0ken"})
    assert r.status_code == 200 and r.json()["director"] == "fallback"


def test_image_failure_is_a_502_with_reason():
    ol, _ = fake_ollama(image_status=404)
    with TestClient(create_app(settings(), ol)) as c:
        r = c.post("/render", json={"theme": "x"}, headers={"Authorization": "Bearer t0ken"})
    assert r.status_code == 502 and "model not found" in r.json()["detail"]


def test_no_llm_installed_uses_fallback():
    ol, _ = fake_ollama(models=[{"name": "x/z-image-turbo:latest", "capabilities": ["image"]}])
    with TestClient(create_app(settings(), ol)) as c:
        r = c.post("/render", json={"theme": "owls"}, headers={"Authorization": "Bearer t0ken"})
    assert r.status_code == 200 and r.json()["director"] == "fallback"


def test_build_prompt_always_appends_style_and_constraint():
    p = art_director.build_image_prompt("a heron.", get_style("swiss"))
    assert p.count("..") == 0 and "International Typographic Style" in p and "no text" in p


def test_falls_back_to_openai_images_route_when_native_is_missing():
    ol, seen = fake_ollama(native=False)
    with TestClient(create_app(settings(), ol)) as c:
        r = c.post("/render", json={"theme": "x"}, headers={"Authorization": "Bearer t0ken"})
    assert r.status_code == 200 and seen["image"]["size"] == "1024x768"


# -- mflux backend -------------------------------------------------------------

import os
import stat
import sys

from mezzotint.studio.mflux_backend import MfluxImager

FAKE_MFLUX = """#!{py}
import sys
from PIL import Image
a = sys.argv[1:]
arg = lambda k: a[a.index(k) + 1]
if "fail" in arg("--prompt"):
    print("RuntimeError: out of memory", flush=True)
    sys.exit(3)
Image.new("RGB", (int(arg("--width")), int(arg("--height"))), (10, 20, 30)).save(arg("--output"))
open(arg("--output") + ".args", "w").write(" ".join(a))
"""


@pytest.fixture
def fake_mflux(tmp_path):
    exe = tmp_path / "mflux-generate-z-image-turbo"
    exe.write_text(FAKE_MFLUX.format(py=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return MfluxImager("filipstrand/Z-Image-Turbo-mflux-4bit", command=str(exe))


def test_mflux_render_end_to_end(fake_mflux):
    ol, seen = fake_ollama()
    s = settings(image_backend="mflux", image_model="filipstrand/Z-Image-Turbo-mflux-4bit")
    with TestClient(create_app(s, ol, fake_mflux)) as c:
        h = c.get("/health", headers={"Authorization": "Bearer t0ken"}).json()
        assert h["image_backend"] == "mflux" and h["image_model_installed"] is True
        r = c.post("/render", json={"theme": "owls", "style_id": "etching"}, headers={"Authorization": "Bearer t0ken"})
    assert r.status_code == 200, r.text
    assert (r.json()["width"], r.json()["height"]) == (1024, 768)
    assert "image" not in seen  # Ollama was only used for the art director


def test_mflux_failure_surfaces_its_last_lines(fake_mflux):
    import asyncio
    with pytest.raises(Exception) as e:
        asyncio.run(fake_mflux.generate("please fail", "512x512"))
    assert "out of memory" in str(e.value) and "exit 3" in str(e.value)


def test_mflux_missing_command_is_reported(tmp_path):
    import asyncio
    im = MfluxImager(command=str(tmp_path / "nope"))
    assert im.available() is False
    with pytest.raises(Exception) as e:
        asyncio.run(im.generate("x", "512x512"))
    assert "not found" in str(e.value)
