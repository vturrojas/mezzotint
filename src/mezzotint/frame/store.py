"""On-disk state: settings, the edition counter and the archive of every print."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..styles import DEFAULT_STYLE

PLATE_KEYS = ("screen", "red", "contrast", "gamma", "sharpen")


@dataclass
class Settings:
    theme: str = ""
    style_id: str = DEFAULT_STYLE
    cadence_min: int = 60  # 0 = only on demand
    matte: str = "matted"
    plate: dict = field(default_factory=dict)  # per-key overrides of the style's plate
    rotate_when_offline: bool = True
    quiet_start: int | None = None  # hour 0-23; no new prints between start and end
    quiet_end: int | None = None


class Store:
    ARCHIVE_LIMIT = 400  # oldest non-favourites are pruned past this

    def __init__(self, root: Path):
        self.root = Path(root)
        self.prints = self.root / "prints"
        self.prints.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._state_path = self.root / "state.json"
        self._index_path = self.root / "archive.json"
        raw = self._read(self._state_path, {})
        known = Settings.__dataclass_fields__
        self.settings = Settings(**{k: v for k, v in raw.get("settings", {}).items() if k in known})
        self.edition = int(raw.get("edition", 0))
        self.on_panel: str | None = raw.get("on_panel")
        self.next_run: float | None = raw.get("next_run")
        self.archive: list[dict] = self._read(self._index_path, [])

    # -- persistence -------------------------------------------------------
    @staticmethod
    def _read(path: Path, default):
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    @staticmethod
    def _write(path: Path, data) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, path)  # atomic on the same filesystem: safe on power loss

    def save(self) -> None:
        with self._lock:
            self._write(
                self._state_path,
                {"settings": asdict(self.settings), "edition": self.edition,
                 "on_panel": self.on_panel, "next_run": self.next_run},
            )
            self._write(self._index_path, self.archive)

    def update_settings(self, patch: dict) -> Settings:
        with self._lock:
            for k, v in patch.items():
                if k == "plate" and isinstance(v, dict):
                    plate = dict(self.settings.plate)
                    for pk, pv in v.items():
                        if pk in PLATE_KEYS:
                            if pv is None:
                                plate.pop(pk, None)
                            else:
                                plate[pk] = pv
                    self.settings.plate = plate
                elif k in Settings.__dataclass_fields__ and k != "plate":
                    setattr(self.settings, k, v)
            self.save()
            return self.settings

    # -- archive -------------------------------------------------------------
    def next_edition(self) -> int:
        with self._lock:
            self.edition += 1
            self.save()  # never reuse a number, even if the print fails
            return self.edition

    def add(self, entry: dict) -> dict:
        with self._lock:
            self.archive.insert(0, entry)
            self._prune()
            self.save()
            return entry

    def get(self, pid: str) -> dict | None:
        return next((e for e in self.archive if e["id"] == pid), None)

    def path(self, pid: str, kind: str) -> Path:
        return self.prints / f"{pid}.{kind}"

    def update(self, pid: str, **fields) -> dict | None:
        with self._lock:
            e = self.get(pid)
            if e:
                e.update(fields)
                self.save()
            return e

    def delete(self, pid: str) -> bool:
        with self._lock:
            e = self.get(pid)
            if not e:
                return False
            self.archive.remove(e)
            for kind in ("source.jpg", "plate.png"):
                self.path(pid, kind).unlink(missing_ok=True)
            if self.on_panel == pid:
                self.on_panel = None
            self.save()
            return True

    def _prune(self) -> None:
        while len(self.archive) > self.ARCHIVE_LIMIT:
            victims = [e for e in self.archive if not e.get("favorite") and e["id"] != self.on_panel]
            if not victims:
                return
            v = victims[-1]
            self.archive.remove(v)
            for kind in ("source.jpg", "plate.png"):
                self.path(v["id"], kind).unlink(missing_ok=True)

    def recent_titles(self, n: int = 12) -> list[str]:
        return [e["title"] for e in self.archive[:n] if e.get("title")]

    @staticmethod
    def new_id() -> str:
        return time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
