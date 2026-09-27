"""Mezzotint Frame — the print head, running on the Raspberry Pi.

Serves the phone app, keeps the schedule, asks the studio for new artwork,
separates it into a three-ink plate and pushes it to the Inky panel. If the
studio is asleep the frame keeps working: it re-pulls favourites from its own
archive so the wall never goes stale.

Run:  mezzotint-frame            (reads settings from the environment)
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import random
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from .. import __version__, ink
from ..styles import STYLES, get_style
from .display import open_display
from .store import PLATE_KEYS, Store

log = logging.getLogger("mezzotint.frame")
STATIC = Path(__file__).parent / "static"


class Studio:
    """Client for the Mac-side studio."""

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._health: dict | None = None
        self._health_at = 0.0

    async def health(self, max_age: float = 20.0) -> dict:
        if self._health is not None and time.monotonic() - self._health_at < max_age:
            return self._health
        try:
            async with httpx.AsyncClient(timeout=6, trust_env=False) as c:
                r = await c.get(f"{self.url}/health", headers=self.headers)
            h = r.json() if r.status_code == 200 else {"ok": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:  # noqa: BLE001
            h = {"ok": False, "error": type(e).__name__}
        h["online"] = bool(h.get("ok"))
        self._health, self._health_at = h, time.monotonic()
        return h

    async def render(self, theme: str, style_id: str, recent: list[str], scene: str | None = None) -> dict:
        body = {"theme": theme, "style_id": style_id, "recent_titles": recent}
        if scene:
            body["scene"] = scene
        # The first request after a Mac reboot loads models: be patient.
        async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=8), trust_env=False) as c:
            r = await c.post(f"{self.url}/render", json=body, headers=self.headers)
        if r.status_code != 200:
            detail = r.json().get("detail", r.text) if "json" in r.headers.get("content-type", "") else r.text
            raise RuntimeError(f"studio: {detail}")
        self._health_at = 0.0
        return r.json()


class Press:
    """Owns the panel and the one-job-at-a-time pipeline."""

    def __init__(self, store: Store, display, studio: Studio, public_url: str):
        self.store = store
        self.display = display
        self.studio = studio
        self.public_url = public_url
        self.lock = asyncio.Lock()
        self.phase = "idle"
        self.message = ""
        self.since = time.time()
        self.last_error: str | None = None

    # -- status ---------------------------------------------------------------
    def set_phase(self, phase: str, message: str = "") -> None:
        self.phase, self.message, self.since = phase, message, time.time()
        log.info("%s %s", phase, message)

    # -- plates ---------------------------------------------------------------
    def plate_settings(self, style_id: str, overrides: dict | None = None, focus=(0.5, 0.5)) -> ink.PlateSettings:
        base = asdict(get_style(style_id).plate)
        base.update({k: v for k, v in self.store.settings.plate.items() if k in PLATE_KEYS})
        base.update({k: v for k, v in (overrides or {}).items() if k in PLATE_KEYS and v is not None})
        matte = (overrides or {}).get("matte") or self.store.settings.matte
        return ink.PlateSettings(**base, matte=matte, focus_x=focus[0], focus_y=focus[1]).clamp()

    def make_plate(self, entry: dict, overrides: dict | None = None):
        src = self.store.path(entry["id"], "source.jpg")
        with Image.open(src) as im:
            im.load()
            focus = (entry.get("focus_x", 0.5), entry.get("focus_y", 0.5))
            s = self.plate_settings(entry["style_id"], overrides, focus)
            plate = ink.render(im, self.display.size, s, entry.get("title", ""), entry.get("edition"))
        return plate, s

    async def _to_panel(self, plate, label: str) -> None:
        self.set_phase("printing", label)
        await asyncio.to_thread(self.display.show, plate)

    # -- jobs -----------------------------------------------------------------
    async def pull(self, reason: str = "manual", scene: str | None = None) -> dict | None:
        """Commission a brand-new print from the studio and put it on the panel."""
        if self.lock.locked():
            return None
        async with self.lock:
            st = self.store.settings
            try:
                self.set_phase("commissioning", f"asking the studio for a {get_style(st.style_id).name} print")
                res = await self.studio.render(st.theme, st.style_id, self.store.recent_titles(), scene)
                pid = self.store.new_id()
                png = base64.b64decode(res["image_png_b64"])
                with Image.open(io.BytesIO(png)) as im:
                    im.convert("RGB").save(self.store.path(pid, "source.jpg"), quality=90)
                entry = {
                    "id": pid,
                    "edition": self.store.next_edition(),
                    "title": res.get("title", "Untitled"),
                    "note": res.get("note", ""),
                    "scene": res.get("scene", ""),
                    "prompt": res.get("prompt", ""),
                    "theme": st.theme,
                    "style_id": res.get("style_id", st.style_id),
                    "director": res.get("director", ""),
                    "image_model": res.get("image_model", ""),
                    "seconds": res.get("seconds", {}),
                    "created": time.time(),
                    "reason": reason,
                    "favorite": False,
                }
                self.set_phase("separating", entry["title"])
                plate, s = await asyncio.to_thread(self.make_plate, entry)
                entry["plate"] = asdict(s)
                entry["coverage"] = ink.ink_coverage(plate)
                ink.to_preview(plate).save(self.store.path(pid, "plate.png"))
                self.store.add(entry)
                await self._to_panel(plate, entry["title"])
                self.store.on_panel = pid
                self.store.save()
                self.last_error = None
                self.set_phase("idle", f"Nº {entry['edition']:03d} is on the wall")
                return entry
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)[:300]
                self.set_phase("error", self.last_error)
                log.exception("pull failed")
                return None

    async def reprint(self, pid: str, overrides: dict | None = None) -> dict:
        async with self.lock:
            entry = self.store.get(pid)
            if not entry:
                raise HTTPException(404, "no such print")
            try:
                self.set_phase("separating", entry["title"])
                plate, s = await asyncio.to_thread(self.make_plate, entry, overrides)
                ink.to_preview(plate).save(self.store.path(pid, "plate.png"))
                self.store.update(pid, plate=asdict(s), coverage=ink.ink_coverage(plate))
                await self._to_panel(plate, entry["title"])
                self.store.on_panel = pid
                self.store.save()
                self.set_phase("idle", f"Nº {entry['edition']:03d} is on the wall")
                return entry
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                self.set_phase("error", str(e)[:300])
                raise

    async def welcome(self) -> None:
        async with self.lock:
            plate = await asyncio.to_thread(ink.welcome_card, self.display.size, self.public_url)
            await self._to_panel(plate, "welcome card")
            self.store.on_panel = None
            self.store.save()
            self.set_phase("idle", "scan the frame to begin")

    # -- schedule -------------------------------------------------------------
    def in_quiet_hours(self, now: datetime | None = None) -> bool:
        st = self.store.settings
        if st.quiet_start is None or st.quiet_end is None or st.quiet_start == st.quiet_end:
            return False
        h = (now or datetime.now()).hour
        if st.quiet_start < st.quiet_end:
            return st.quiet_start <= h < st.quiet_end
        return h >= st.quiet_start or h < st.quiet_end  # wraps midnight

    def schedule_next(self, from_ts: float | None = None) -> None:
        mins = self.store.settings.cadence_min
        self.store.next_run = (from_ts or time.time()) + mins * 60 if mins > 0 else None
        self.store.save()

    async def rotate_archive(self) -> bool:
        pool = [e for e in self.store.archive if e.get("favorite")] or self.store.archive
        pool = [e for e in pool if e["id"] != self.store.on_panel]
        if not pool:
            return False
        await self.reprint(random.choice(pool)["id"])
        return True

    async def run_forever(self) -> None:
        if self.store.settings.cadence_min > 0 and not self.store.next_run:
            self.schedule_next()
        while True:
            await asyncio.sleep(15)
            nr = self.store.next_run
            if not nr or time.time() < nr or self.lock.locked():
                continue
            self.schedule_next()
            if self.in_quiet_hours():
                continue
            health = await self.studio.health(max_age=0)
            if health.get("online"):
                entry = await self.pull("schedule")
                if entry is None and self.store.settings.rotate_when_offline:
                    await self.rotate_archive()
            elif self.store.settings.rotate_when_offline:
                log.info("studio offline; rotating the archive")
                await self.rotate_archive()


def _public_url() -> str:
    url = os.environ.get("MEZZOTINT_PUBLIC_URL")
    if url:
        return url
    port = int(os.environ.get("MEZZOTINT_FRAME_PORT", "80"))
    host = f"{socket.gethostname()}.local"
    return f"http://{host}/" if port == 80 else f"http://{host}:{port}/"


def create_app(data_dir: Path | None = None, display=None, studio: Studio | None = None, run_scheduler: bool = True) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("MEZZOTINT_DATA", Path.home() / "mezzotint-data"))
    store = Store(data_dir)
    display = display or open_display(data_dir)
    studio = studio or Studio(os.environ.get("MEZZOTINT_STUDIO_URL", "http://studio-mac.local:8765"),
                              os.environ.get("MEZZOTINT_TOKEN", ""))
    press = Press(store, display, studio, _public_url())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = []
        if not store.archive:
            tasks.append(asyncio.create_task(press.welcome()))
        if run_scheduler:
            tasks.append(asyncio.create_task(press.run_forever()))
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="Mezzotint Frame", version=__version__, lifespan=lifespan)
    app.state.press = press

    def entry_view(e: dict | None) -> dict | None:
        if not e:
            return None
        v = {k: e.get(k) for k in ("id", "edition", "title", "note", "scene", "prompt", "theme", "style_id",
                                   "director", "image_model", "seconds", "created", "favorite", "plate", "coverage")}
        pp = store.path(e["id"], "plate.png")
        stamp = int(pp.stat().st_mtime) if pp.exists() else 0
        v["plate_url"] = f"/api/prints/{e['id']}/plate.png?v={stamp}"
        v["source_url"] = f"/api/prints/{e['id']}/source.jpg"
        return v

    @app.get("/api/state")
    async def state():
        health = await studio.health()
        return {
            "version": __version__,
            "settings": asdict(store.settings),
            "status": {"phase": press.phase, "message": press.message, "since": press.since,
                       "error": press.last_error, "next_run": store.next_run,
                       "quiet": press.in_quiet_hours()},
            "studio": {k: health.get(k) for k in ("online", "llm", "image_model", "image_model_installed", "busy", "error")},
            "panel": {"width": display.size[0], "height": display.size[1], "kind": getattr(display, "name", "?")},
            "current": entry_view(store.get(store.on_panel) if store.on_panel else None),
            "edition": store.edition,
            "public_url": press.public_url,
        }

    @app.get("/api/catalog")
    async def catalog():
        return {
            "styles": [s.public() for s in STYLES],
            "screens": [{"id": k, "name": v} for k, v in ink.SCREENS.items()],
            "mattes": list(ink.MATTES),
        }

    @app.put("/api/settings")
    async def put_settings(req: Request):
        patch = await req.json()
        allowed = {"theme", "style_id", "cadence_min", "matte", "plate", "rotate_when_offline", "quiet_start", "quiet_end"}
        patch = {k: v for k, v in patch.items() if k in allowed}
        if "theme" in patch:
            patch["theme"] = str(patch["theme"])[:300]
        if "cadence_min" in patch:
            patch["cadence_min"] = max(0, min(24 * 60, int(patch["cadence_min"])))
        if "style_id" in patch and patch["style_id"] not in {s.id for s in STYLES}:
            raise HTTPException(400, "unknown style")
        s = store.update_settings(patch)
        if "cadence_min" in patch:
            press.schedule_next()
        return asdict(s)

    @app.post("/api/pull")
    async def pull(req: Request):
        if press.lock.locked():
            raise HTTPException(409, "the press is busy")
        body = await req.json() if req.headers.get("content-length", "0") != "0" else {}
        scene = (body or {}).get("scene")
        asyncio.create_task(press.pull("manual", scene[:400] if scene else None))
        press.schedule_next()
        return {"ok": True}

    @app.get("/api/prints")
    async def prints(limit: int = 60, offset: int = 0, favorites: bool = False):
        items = [e for e in store.archive if e.get("favorite")] if favorites else store.archive
        return {"total": len(items), "items": [entry_view(e) for e in items[offset:offset + limit]]}

    @app.get("/api/prints/{pid}/plate.png")
    async def plate_png(pid: str):
        p = store.path(pid, "plate.png")
        if not store.get(pid) or not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/prints/{pid}/source.jpg")
    async def source_jpg(pid: str):
        p = store.path(pid, "source.jpg")
        if not store.get(pid) or not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/api/prints/{pid}/proof.png")
    async def proof(pid: str, request: Request):
        """Render a plate with trial settings for the phone, without touching the panel."""
        e = store.get(pid)
        if not e:
            raise HTTPException(404)
        q = request.query_params
        ov: dict = {}
        for k in ("red", "contrast", "gamma", "sharpen"):
            if k in q:
                ov[k] = float(q[k])
        for k in ("screen", "matte"):
            if k in q:
                ov[k] = q[k]
        entry = dict(e)
        if "style_id" in q:
            entry["style_id"] = q["style_id"]
        plate, _ = await asyncio.to_thread(press.make_plate, entry, ov)
        buf = io.BytesIO()
        ink.to_preview(plate).save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/prints/{pid}/print")
    async def print_again(pid: str, req: Request):
        if press.lock.locked():
            raise HTTPException(409, "the press is busy")
        if not store.get(pid):
            raise HTTPException(404)
        body = await req.json() if req.headers.get("content-length", "0") != "0" else {}
        asyncio.create_task(press.reprint(pid, body or None))
        return {"ok": True}

    @app.post("/api/prints/{pid}/favorite")
    async def favorite(pid: str, req: Request):
        body = await req.json()
        e = store.update(pid, favorite=bool(body.get("favorite")))
        if not e:
            raise HTTPException(404)
        return {"favorite": e["favorite"]}

    @app.delete("/api/prints/{pid}")
    async def delete(pid: str):
        if not store.delete(pid):
            raise HTTPException(404)
        return {"ok": True}

    @app.post("/api/welcome")
    async def welcome():
        if press.lock.locked():
            raise HTTPException(409, "the press is busy")
        asyncio.create_task(press.welcome())
        return {"ok": True}

    swatch_cache: dict[str, bytes] = {}

    @app.get("/api/swatches/{style_id}.png")
    async def swatch(style_id: str):
        """A real plate of the sampler scene in each style, for the style picker."""
        if style_id not in {s.id for s in STYLES}:
            raise HTTPException(404)
        if style_id not in swatch_cache:
            from ..proof import style_swatch

            plate = await asyncio.to_thread(style_swatch, style_id)
            buf = io.BytesIO()
            ink.to_preview(plate).save(buf, "PNG")
            swatch_cache[style_id] = buf.getvalue()
        return Response(swatch_cache[style_id], media_type="image/png",
                        headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/api/panel.png")
    async def panel_png():
        """What should be on the glass right now (for the phone's hero view)."""
        if store.on_panel and store.path(store.on_panel, "plate.png").exists():
            return FileResponse(store.path(store.on_panel, "plate.png"), headers={"Cache-Control": "no-store"})
        plate = ink.welcome_card(display.size, press.public_url)
        buf = io.BytesIO()
        ink.to_preview(plate).save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled")
        return JSONResponse({"detail": str(exc)[:300]}, status_code=500)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    port = int(os.environ.get("MEZZOTINT_FRAME_PORT", "80"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
