"""Panel drivers: the real Inky wHAT/pHAT, or a virtual panel that writes PNGs."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

from .. import ink

log = logging.getLogger("mezzotint.frame.display")


class VirtualDisplay:
    """Stands in for the panel on a laptop: every 'refresh' writes a PNG."""

    name = "virtual"
    colour = "red"

    def __init__(self, out_dir: Path, size: tuple[int, int] = (400, 300)):
        self.size = size
        self.out = Path(out_dir) / "virtual-panel.png"

    def show(self, plate: np.ndarray) -> None:
        ink.to_preview(plate, 2).save(self.out)
        log.info("virtual panel refreshed -> %s", self.out)


class InkyDisplay:
    """Pimoroni Inky. Auto-detects from the board's EEPROM; set
    MEZZOTINT_PANEL=what-red (or phat-red) for boards without one."""

    def __init__(self) -> None:
        panel = os.environ.get("MEZZOTINT_PANEL", "auto")
        if panel == "auto":
            from inky.auto import auto

            self.dev = auto(ask_user=False, verbose=True)
        else:
            kind, colour = panel.split("-", 1)
            if kind == "what":
                from inky import InkyWHAT as Cls
            else:
                from inky import InkyPHAT as Cls
            self.dev = Cls(colour)
        self.size = tuple(self.dev.resolution)
        self.colour = getattr(self.dev, "colour", "red")
        self.name = type(self.dev).__name__
        if self.colour not in ("red", "yellow"):
            log.warning("panel colour is %r; red plates will print as black", self.colour)

    def show(self, plate: np.ndarray) -> None:
        img = ink.to_palette_image(plate)
        if self.colour not in ("red", "yellow"):
            arr = np.asarray(img).copy()
            arr[arr == ink.RED] = ink.BLACK
            img = ink.to_palette_image(arr)
        self.dev.set_border(self.dev.WHITE)
        self.dev.set_image(img)
        self.dev.show()  # blocking: tri-colour panels take ~15-30 s


def open_display(data_dir: Path):
    kind = os.environ.get("MEZZOTINT_DISPLAY", "auto")
    if kind == "virtual":
        w, h = (int(v) for v in os.environ.get("MEZZOTINT_VIRTUAL_SIZE", "400x300").split("x"))
        return VirtualDisplay(data_dir, (w, h))
    try:
        d = InkyDisplay()
        log.info("panel: %s %s %sx%s", d.name, d.colour, *d.size)
        return d
    except Exception as e:  # noqa: BLE001
        if kind == "inky":
            raise
        log.warning("no Inky panel found (%s); using a virtual panel", e)
        return VirtualDisplay(data_dir)
