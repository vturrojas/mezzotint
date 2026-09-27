"""A procedural sampler scene plus the `mezzotint-proof` contact-sheet tool.

The sampler is drawn, not generated, so style swatches exist before any
model has run: a sun, layered hills, water and a boat, with a tonal sky.
The proof tool runs any image through every style (or every screen) and lays
the plates out on one sheet, so a designer can compare separations side by
side the way you'd compare test pulls.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import ink
from .styles import STYLES


@lru_cache(maxsize=1)
def sampler(size: tuple[int, int] = (800, 600)) -> Image.Image:
    w, h = size
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(250 - 70 * t), int(236 - 110 * t), int(214 - 120 * t)))
    d.ellipse((w * 0.56, h * 0.14, w * 0.80, h * 0.46), fill=(222, 52, 34))
    d.polygon([(0, h * .62), (w * .2, h * .40), (w * .38, h * .58), (w * .58, h * .36), (w * .8, h * .6),
               (w, h * .48), (w, h), (0, h)], fill=(96, 92, 110))
    d.polygon([(0, h * .78), (w * .3, h * .60), (w * .55, h * .74), (w * .82, h * .58), (w, h * .70),
               (w, h), (0, h)], fill=(34, 36, 52))
    d.rectangle((0, h * .84, w, h), fill=(70, 112, 160))
    d.polygon([(w * .40, h * .93), (w * .47, h * .74), (w * .47, h * .93)], fill=(205, 60, 44))
    d.line([(w * .36, h * .94), (w * .52, h * .94)], fill=(20, 20, 24), width=max(2, h // 90))
    return im.filter(ImageFilter.GaussianBlur(1.5))


def style_swatch(style_id: str, size: tuple[int, int] = (160, 120)):
    style = next(s for s in STYLES if s.id == style_id)
    s = ink.PlateSettings(**asdict(style.plate), matte="bleed")
    return ink.render(sampler(), size, s)


def _label(img: Image.Image, text: str) -> Image.Image:
    out = Image.new("RGB", (img.width, img.height + 22), (250, 248, 243))
    out.paste(img, (0, 0))
    d = ImageDraw.Draw(out)
    text = text.replace("–", "-").replace("×", "x").replace("°", " deg")  # bitmap default font is ASCII
    d.text((2, img.height + 5), text, fill=(30, 29, 33), font=ImageFont.load_default())
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Contact sheet of three-ink plates for an image.")
    ap.add_argument("image", nargs="?", help="source image (default: built-in sampler)")
    ap.add_argument("-o", "--out", default="proof-sheet.png")
    ap.add_argument("--size", default="400x300", help="panel size, e.g. 400x300 or 250x122")
    ap.add_argument("--by", choices=["style", "screen"], default="style")
    ap.add_argument("--scale", type=int, default=2)
    a = ap.parse_args()

    size = tuple(int(v) for v in a.size.split("x"))
    src = Image.open(a.image).convert("RGB") if a.image else sampler()
    tiles = []
    if a.by == "style":
        for st in STYLES:
            p = ink.render(src, size, ink.PlateSettings(**asdict(st.plate)))
            tiles.append(_label(ink.to_preview(p, a.scale), f"{st.name}  ·  {ink.SCREENS[st.plate.screen]}"))
    else:
        for key, name in ink.SCREENS.items():
            p = ink.render(src, size, ink.PlateSettings(screen=key))
            tiles.append(_label(ink.to_preview(p, a.scale), name))
    cols = 2
    tw, th = tiles[0].size
    rows = (len(tiles) + cols - 1) // cols
    gap = 16
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * gap, rows * th + (rows + 1) * gap), (250, 248, 243))
    for i, t in enumerate(tiles):
        sheet.paste(t, (gap + (i % cols) * (tw + gap), gap + (i // cols) * (th + gap)))
    sheet.save(a.out)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
