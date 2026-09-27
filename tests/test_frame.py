import asyncio
import base64
import io
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mezzotint.frame.display import VirtualDisplay
from mezzotint.frame.server import Press, create_app
from mezzotint.frame.store import Store
from mezzotint.proof import sampler


class FakeStudio:
    def __init__(self, online=True):
        self.online = online
        self.calls = []

    async def health(self, max_age=20.0):
        return {"ok": self.online, "online": self.online, "llm": "fake", "image_model": "fake-img"}

    async def render(self, theme, style_id, recent, scene=None):
        if not self.online:
            raise RuntimeError("studio: connection refused")
        self.calls.append((theme, style_id, list(recent)))
        buf = io.BytesIO()
        sampler((1024, 768)).save(buf, "PNG")
        return {"title": f"Print {len(self.calls)}", "note": "n", "scene": "s", "prompt": "p",
                "style_id": style_id, "director": "fake", "image_model": "fake-img",
                "seconds": {"direction": 1.0, "image": 2.0}, "image_png_b64": base64.b64encode(buf.getvalue()).decode()}


def wait_booted(c, tmp, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if (tmp / "virtual-panel.png").exists() and c.get("/api/state").json()["status"]["phase"] == "idle":
            return
        time.sleep(0.05)
    raise AssertionError("welcome card never printed")


def wait_idle(c, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = c.get("/api/state").json()
        if s["status"]["phase"] in ("idle", "error") and (s["current"] or s["status"]["phase"] == "error"):
            return s
        time.sleep(0.1)
    raise AssertionError("press never went idle")


@pytest.fixture
def frame(tmp_path):
    studio = FakeStudio()
    app = create_app(tmp_path, VirtualDisplay(tmp_path, (400, 300)), studio, run_scheduler=False)
    with TestClient(app) as c:
        wait_booted(c, tmp_path)
        yield c, studio, tmp_path, app


def test_first_boot_prints_welcome_card(frame):
    c, _, tmp, _ = frame
    assert (tmp / "virtual-panel.png").exists()
    assert c.get("/api/panel.png").status_code == 200


def test_settings_roundtrip_and_validation(frame):
    c, *_ = frame
    r = c.put("/api/settings", json={"theme": "owls", "style_id": "etching", "cadence_min": 99999, "evil": 1})
    assert r.status_code == 200
    s = r.json()
    assert s["theme"] == "owls" and s["style_id"] == "etching" and s["cadence_min"] == 1440 and "evil" not in s
    assert c.put("/api/settings", json={"style_id": "nope"}).status_code == 400


def test_pull_prints_archives_and_passes_recent_titles(frame):
    c, studio, tmp, _ = frame
    c.put("/api/settings", json={"theme": "tide pools", "style_id": "riso"})
    c.post("/api/pull")
    s = wait_idle(c)
    assert s["current"]["edition"] == 1 and s["current"]["title"] == "Print 1"
    assert set(s["current"]["coverage"]) == {"paper", "black", "red"}
    assert c.post("/api/pull").status_code == 200
    t0 = time.time()
    while c.get("/api/state").json()["edition"] < 2 and time.time() - t0 < 20:
        time.sleep(0.1)
    s = wait_idle(c)
    assert s["current"]["edition"] == 2
    assert studio.calls[-1][2] == ["Print 1"]  # recent titles fed back for variety
    items = c.get("/api/prints").json()["items"]
    assert [e["edition"] for e in items] == [2, 1]
    assert c.get(items[0]["plate_url"]).status_code == 200
    assert c.get(items[0]["source_url"]).status_code == 200


def test_proof_does_not_touch_panel_and_reprint_does(frame):
    c, _, tmp, _ = frame
    c.post("/api/pull")
    s = wait_idle(c)
    pid = s["current"]["id"]
    panel = tmp / "virtual-panel.png"
    before = panel.stat().st_mtime_ns
    r = c.get(f"/api/prints/{pid}/proof.png?screen=halftone&red=0.2&matte=bleed")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert panel.stat().st_mtime_ns == before
    c.post(f"/api/prints/{pid}/print", json={"screen": "threshold"})
    t0 = time.time()
    while panel.stat().st_mtime_ns == before and time.time() - t0 < 10:
        time.sleep(0.1)
    assert panel.stat().st_mtime_ns != before
    wait_idle(c)
    assert c.get("/api/prints").json()["items"][0]["plate"]["screen"] == "threshold"


def test_favorite_delete_and_404s(frame):
    c, *_ = frame
    c.post("/api/pull")
    pid = wait_idle(c)["current"]["id"]
    assert c.post(f"/api/prints/{pid}/favorite", json={"favorite": True}).json()["favorite"] is True
    assert c.get("/api/prints?favorites=true").json()["total"] == 1
    assert c.delete(f"/api/prints/{pid}").status_code == 200
    assert c.get(f"/api/prints/{pid}/plate.png").status_code == 404
    assert c.get("/api/prints/../../etc/passwd/plate.png").status_code == 404


def test_offline_studio_surfaces_error(tmp_path):
    app = create_app(tmp_path, VirtualDisplay(tmp_path), FakeStudio(online=False), run_scheduler=False)
    with TestClient(app) as c:
        wait_booted(c, tmp_path)
        c.post("/api/pull")
        s = wait_idle(c)
        assert s["status"]["phase"] == "error" and "connection refused" in s["status"]["error"]


def test_swatches_render_for_every_style(frame):
    c, *_ = frame
    styles = c.get("/api/catalog").json()["styles"]
    for st in styles:
        assert c.get(f"/api/swatches/{st['id']}.png").status_code == 200


# -- scheduler logic, exercised directly ---------------------------------------

def make_press(tmp_path, online=True):
    store = Store(tmp_path)
    return Press(store, VirtualDisplay(tmp_path), FakeStudio(online), "http://x.local/"), store


def test_quiet_hours_including_midnight_wrap(tmp_path):
    press, store = make_press(tmp_path)
    store.settings.quiet_start, store.settings.quiet_end = 22, 7
    assert press.in_quiet_hours(datetime(2026, 1, 1, 23))
    assert press.in_quiet_hours(datetime(2026, 1, 1, 3))
    assert not press.in_quiet_hours(datetime(2026, 1, 1, 12))
    store.settings.quiet_start, store.settings.quiet_end = 1, 5
    assert press.in_quiet_hours(datetime(2026, 1, 1, 2)) and not press.in_quiet_hours(datetime(2026, 1, 1, 6))


def test_rotation_prefers_favourites_when_studio_sleeps(tmp_path):
    async def go():
        press, store = make_press(tmp_path)
        for _ in range(3):
            await press.pull()
        fav = store.archive[-1]["id"]
        store.update(fav, favorite=True)
        store.on_panel = store.archive[0]["id"]
        press.studio.online = False
        assert await press.rotate_archive()
        return store.on_panel, fav

    on_panel, fav = asyncio.run(go())
    assert on_panel == fav


def test_store_persists_and_survives_restart(tmp_path):
    s = Store(tmp_path)
    s.update_settings({"theme": "foxes", "plate": {"red": 0.2, "bogus": 1}})
    s.next_edition()
    s2 = Store(tmp_path)
    assert s2.settings.theme == "foxes" and s2.settings.plate == {"red": 0.2} and s2.edition == 1
    s2.update_settings({"plate": {"red": None}})
    assert Store(tmp_path).settings.plate == {}
