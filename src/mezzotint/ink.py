"""The ink engine.

Turns an arbitrary RGB image into a three-ink plate (paper, black, red) for a
tri-colour e-paper panel, the way a print shop would separate artwork for a
two-colour press:

1. Fit   – cover-crop to the panel's aspect around a focal point, resample.
2. Tone  – auto-levels, contrast curve, gamma, unsharp mask.
3. Separate – move every pixel into *ink space*: a 2-D coordinate of
   (lightness, redness). Redness comes from CIELAB hue proximity to vermilion
   and chroma, so blues and greens become neutral greys (black screen) while
   reds, oranges and warm magentas are routed to the red plate.
4. Screen – quantise ink space to the three inks with one of several screens:
   error diffusion (Floyd–Steinberg, Atkinson, Jarvis, Stucki, Sierra Lite),
   ordered Bayer, rotated clustered-dot halftone (45° black / 15° red, like a
   real two-drum risograph), or a hard threshold for relief-print looks.
5. Mount  – optional matte, plate mark and a pixel-type colophon.

Everything here is NumPy + Pillow so it runs on a Raspberry Pi 3.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image, ImageFilter

# Ink indices match the Inky library: WHITE=0, BLACK=1, RED=2.
PAPER, BLACK, RED = 0, 1, 2

# What the physical inks look like, for on-phone proofs. Calibrated against an
# Inky wHAT (red): the "white" is a neutral, slightly cool e-paper grey and the
# red is a clean vermilion, so proofs on the phone match the wall.
PREVIEW_INKS = {
    PAPER: (226, 227, 224),
    BLACK: (36, 36, 40),
    RED: (198, 38, 32),
}

# Ink positions in (lightness, redness) space.
INK_POINTS = {PAPER: (1.0, 0.0), BLACK: (0.0, 0.0), RED: (0.48, 1.0)}
RED_HUE_DEG = 38.0  # CIELAB hue angle of a warm vermilion
RED_FLOOR = 0.22  # redness below this never reaches the red plate
RED_LIGHTNESS_LEAK = 0.15  # share of a red pixel's lightness error passed on

SCREENS = {
    "floyd": "Floyd–Steinberg",
    "atkinson": "Atkinson",
    "jarvis": "Jarvis–Judice–Ninke",
    "stucki": "Stucki",
    "sierra": "Sierra Lite",
    "bayer": "Bayer 8×8",
    "halftone": "Halftone 45°/15°",
    "threshold": "Hard threshold",
}

MATTES = ("bleed", "plate", "matted")

# (dx, dy, weight) diffusion kernels, weights normalised below.
_KERNELS = {
    "floyd": [(1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)],
    # Atkinson deliberately diffuses only 6/8 of the error: crisp highlights.
    "atkinson": [(1, 0, 1), (2, 0, 1), (-1, 1, 1), (0, 1, 1), (1, 1, 1), (0, 2, 1)],
    "jarvis": [
        (1, 0, 7), (2, 0, 5),
        (-2, 1, 3), (-1, 1, 5), (0, 1, 7), (1, 1, 5), (2, 1, 3),
        (-2, 2, 1), (-1, 2, 3), (0, 2, 5), (1, 2, 3), (2, 2, 1),
    ],
    "stucki": [
        (1, 0, 8), (2, 0, 4),
        (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2),
        (-2, 2, 1), (-1, 2, 2), (0, 2, 4), (1, 2, 2), (2, 2, 1),
    ],
    "sierra": [(1, 0, 2), (-1, 1, 1), (0, 1, 1)],
}
_KERNEL_DIVISOR = {"floyd": 16, "atkinson": 8, "jarvis": 48, "stucki": 42, "sierra": 4}


@dataclass(frozen=True)
class PlateSettings:
    screen: str = "atkinson"
    red: float = 0.6
    contrast: float = 1.15
    gamma: float = 1.0
    sharpen: float = 0.6
    matte: str = "bleed"
    focus_x: float = 0.5  # 0..1 focal point for the cover crop
    focus_y: float = 0.5

    def clamp(self) -> "PlateSettings":
        return replace(
            self,
            screen=self.screen if self.screen in SCREENS else "atkinson",
            red=float(min(1.0, max(0.0, self.red))),
            contrast=float(min(2.5, max(0.5, self.contrast))),
            gamma=float(min(2.0, max(0.5, self.gamma))),
            sharpen=float(min(2.0, max(0.0, self.sharpen))),
            matte=self.matte if self.matte in MATTES else "bleed",
            focus_x=float(min(1.0, max(0.0, self.focus_x))),
            focus_y=float(min(1.0, max(0.0, self.focus_y))),
        )


# --------------------------------------------------------------------------
# 1. Fit
# --------------------------------------------------------------------------

def fit(img: Image.Image, size: tuple[int, int], fx: float = 0.5, fy: float = 0.5) -> Image.Image:
    """Cover-crop to the target aspect ratio around a focal point, then resample."""
    img = img.convert("RGB")
    tw, th = size
    sw, sh = img.size
    target = tw / th
    if sw / sh > target:  # too wide: crop width
        cw, ch = int(round(sh * target)), sh
    else:
        cw, ch = sw, int(round(sw / target))
    left = int(round((sw - cw) * fx))
    top = int(round((sh - ch) * fy))
    img = img.crop((left, top, left + cw, top + ch))
    # Two-step downsample keeps LANCZOS from ringing on huge reductions.
    if cw > tw * 4:
        img = img.reduce(max(1, cw // (tw * 2)))
    return img.resize(size, Image.LANCZOS)


# --------------------------------------------------------------------------
# 2. Tone
# --------------------------------------------------------------------------

def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """rgb float array in 0..1, shape (..., 3) -> CIELAB (D65)."""
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array(
        [[0.4124564, 0.3575761, 0.1804375],
         [0.2126729, 0.7151522, 0.0721750],
         [0.0193339, 0.1191920, 0.9503041]]
    )
    xyz = lin @ m.T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    eps = 216 / 24389
    kappa = 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def _tone(lightness: np.ndarray, contrast: float, gamma: float) -> np.ndarray:
    """Auto-levels, a smooth contrast S-curve and gamma, all on 0..1 lightness."""
    lo, hi = np.percentile(lightness, [1.0, 99.0])
    if hi - lo > 1e-3:
        lightness = (lightness - lo) / (hi - lo)
    lightness = np.clip(lightness, 0.0, 1.0)
    # Contrast as a sigmoid centred on mid grey; 1.0 is identity.
    if abs(contrast - 1.0) > 1e-3:
        k = (contrast - 1.0) * 6.0
        if k > 0:
            s = 1 / (1 + np.exp(-k * (lightness - 0.5)))
            s0, s1 = 1 / (1 + math.exp(k * 0.5)), 1 / (1 + math.exp(-k * 0.5))
            lightness = (s - s0) / (s1 - s0)
        else:
            lightness = 0.5 + (lightness - 0.5) * contrast
    lightness = np.clip(lightness, 0.0, 1.0) ** (1.0 / gamma)
    return lightness


# --------------------------------------------------------------------------
# 3. Separate
# --------------------------------------------------------------------------

def separate(img: Image.Image, s: PlateSettings) -> tuple[np.ndarray, np.ndarray]:
    """Return (lightness, redness) arrays in 0..1 for a fitted RGB image."""
    if s.sharpen > 0:
        img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(s.sharpen * 160), threshold=1))
    rgb = np.asarray(img, dtype=np.float64) / 255.0
    lab = _srgb_to_lab(rgb)
    L = lab[..., 0] / 100.0
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1]))
    dh = np.radians(((hue - RED_HUE_DEG + 180) % 360) - 180)
    # A cosine window around vermilion: oranges and warm magentas count a
    # little, blues and greens never do.
    hue_w = np.clip(np.cos(dh), 0.0, 1.0) ** 4
    redness = hue_w * np.clip((chroma - 24.0) / 36.0, 0.0, 1.0)
    gain = 0.4 + 1.8 * s.red
    redness = np.clip(redness * gain, 0.0, 1.0) if s.red > 0 else np.zeros_like(redness)
    # Red is a spot ink and should be deliberate: faint warmth (a cream horizon,
    # a tan wall) falls below the floor and prints as neutral tone, not speckle.
    redness = np.clip((redness - RED_FLOOR) / (1.0 - RED_FLOOR), 0.0, 1.0)
    lightness = _tone(L, s.contrast, s.gamma)
    # Keep strongly red regions near the red ink's own value so tone curves
    # do not push a red poster shape into black.
    lightness = lightness * (1 - 0.5 * redness) + INK_POINTS[RED][0] * 0.5 * redness
    return lightness, redness


# --------------------------------------------------------------------------
# 4. Screen
# --------------------------------------------------------------------------

_RED_WEIGHT = 1.3  # how much a mismatch in redness costs vs lightness


def _nearest(l: float, r: float) -> int:
    best, best_d = PAPER, 1e9
    for ink, (il, ir) in INK_POINTS.items():
        d = (l - il) ** 2 + _RED_WEIGHT * (r - ir) ** 2
        if d < best_d:
            best, best_d = ink, d
    return best


def _nearest_vec(l: np.ndarray, r: np.ndarray) -> np.ndarray:
    ds = []
    for ink in (PAPER, BLACK, RED):
        il, ir = INK_POINTS[ink]
        ds.append((l - il) ** 2 + _RED_WEIGHT * (r - ir) ** 2)
    return np.argmin(np.stack(ds), axis=0).astype(np.uint8)


def _diffuse(l: np.ndarray, r: np.ndarray, kernel: str) -> np.ndarray:
    """Serpentine error diffusion in ink space. Pure-Python inner loop on lists
    is faster than NumPy scalar indexing and fine for ~30k pixels on a Pi."""
    h, w = l.shape
    L = l.tolist()
    R = r.tolist()
    out = np.zeros((h, w), dtype=np.uint8)
    taps = [(dx, dy, wt / _KERNEL_DIVISOR[kernel]) for dx, dy, wt in _KERNELS[kernel]]
    pts = INK_POINTS
    red = RED
    for y in range(h):
        rev = y % 2 == 1
        xs = range(w - 1, -1, -1) if rev else range(w)
        Ly, Ry = L[y], R[y]
        row = out[y]
        for x in xs:
            # Clamp what has accumulated so one bright or saturated region
            # cannot bank error and release it as a halo further down.
            lv = min(max(Ly[x], -0.3), 1.3)
            rv = min(max(Ry[x], -0.3), 1.3)
            ink = _nearest(lv, rv)
            row[x] = ink
            il, ir = pts[ink]
            el, er = lv - il, rv - ir
            if ink == red:
                # Red is a spot ink: its lightness differs from the artwork's
                # reds by design. Passing that difference on would print a
                # pale ghost of every red shape in the tone around it.
                el *= RED_LIGHTNESS_LEAK
            if el == 0.0 and er == 0.0:
                continue
            for dx, dy, wt in taps:
                nx = x - dx if rev else x + dx
                ny = y + dy
                if 0 <= nx < w and ny < h:
                    L[ny][nx] += el * wt
                    R[ny][nx] += er * wt
    return out


def _bayer(n: int = 8) -> np.ndarray:
    m = np.array([[0, 2], [3, 1]])
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return (m + 0.5) / m.size


def _ordered(l: np.ndarray, r: np.ndarray) -> np.ndarray:
    h, w = l.shape
    b = _bayer(8)
    t = np.tile(b, (h // 8 + 1, w // 8 + 1))[:h, :w]
    t2 = np.tile(b.T, (h // 8 + 1, w // 8 + 1))[:h, :w]  # decorrelated for red
    return _nearest_vec(l + (t - 0.5) * 0.9, r + (t2 - 0.5) * 0.9)


def _screen(h: int, w: int, angle_deg: float, cell: float) -> np.ndarray:
    """Clustered-dot screen: threshold in 0..1, low values at dot centres."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    a = math.radians(angle_deg)
    u = (xx * math.cos(a) + yy * math.sin(a)) / cell
    v = (-xx * math.sin(a) + yy * math.cos(a)) / cell
    spot = (np.cos(2 * math.pi * u) + np.cos(2 * math.pi * v)) / 4 + 0.5
    return 1.0 - spot


def _halftone(l: np.ndarray, r: np.ndarray) -> np.ndarray:
    h, w = l.shape
    # Screen ruling scales with the panel so dots stay a few pixels wide.
    cell = max(3.2, w / 100)  # ~4 px dots on a wHAT: fine grain, still reads as riso
    red_screen = _screen(h, w, 15.0, cell * 1.15)
    black_screen = _screen(h, w, 45.0, cell)
    out = np.full((h, w), PAPER, dtype=np.uint8)
    darkness = 1.0 - l
    # Red drum prints first, black overprints where it's darker than red ink.
    is_red = r > red_screen
    out[is_red] = RED
    black_need = np.where(is_red, (darkness - (1 - INK_POINTS[RED][0])) * 2.0, darkness)
    out[black_need > black_screen] = BLACK
    return out


def screen(l: np.ndarray, r: np.ndarray, method: str) -> np.ndarray:
    if method in _KERNELS:
        return _diffuse(l, r, method)
    if method == "bayer":
        return _ordered(l, r)
    if method == "halftone":
        return _halftone(l, r)
    return _nearest_vec(l, r)  # threshold


# --------------------------------------------------------------------------
# 5. Mount: matte, plate mark, colophon in a 3x5 pixel face
# --------------------------------------------------------------------------

_FONT = {
    "A": [".#.", "#.#", "###", "#.#", "#.#"], "B": ["##.", "#.#", "##.", "#.#", "##."],
    "C": [".##", "#..", "#..", "#..", ".##"], "D": ["##.", "#.#", "#.#", "#.#", "##."],
    "E": ["###", "#..", "##.", "#..", "###"], "F": ["###", "#..", "##.", "#..", "#.."],
    "G": [".##", "#..", "#.#", "#.#", ".##"], "H": ["#.#", "#.#", "###", "#.#", "#.#"],
    "I": ["###", ".#.", ".#.", ".#.", "###"], "J": ["..#", "..#", "..#", "#.#", ".#."],
    "K": ["#.#", "#.#", "##.", "#.#", "#.#"], "L": ["#..", "#..", "#..", "#..", "###"],
    "M": ["#...#", "##.##", "#.#.#", "#...#", "#...#"], "N": ["#..#", "##.#", "#.##", "#..#", "#..#"],
    "O": [".#.", "#.#", "#.#", "#.#", ".#."], "P": ["##.", "#.#", "##.", "#..", "#.."],
    "Q": [".#.", "#.#", "#.#", "##.", ".##"], "R": ["##.", "#.#", "##.", "#.#", "#.#"],
    "S": [".##", "#..", ".#.", "..#", "##."], "T": ["###", ".#.", ".#.", ".#.", ".#."],
    "U": ["#.#", "#.#", "#.#", "#.#", "###"], "V": ["#.#", "#.#", "#.#", "#.#", ".#."],
    "W": ["#...#", "#...#", "#.#.#", "##.##", "#...#"], "X": ["#.#", "#.#", ".#.", "#.#", "#.#"],
    "Y": ["#.#", "#.#", ".#.", ".#.", ".#."], "Z": ["###", "..#", ".#.", "#..", "###"],
    "0": ["###", "#.#", "#.#", "#.#", "###"], "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["##.", "..#", ".#.", "#..", "###"], "3": ["##.", "..#", ".#.", "..#", "##."],
    "4": ["#.#", "#.#", "###", "..#", "..#"], "5": ["###", "#..", "##.", "..#", "##."],
    "6": [".##", "#..", "###", "#.#", "###"], "7": ["###", "..#", ".#.", ".#.", ".#."],
    "8": ["###", "#.#", "###", "#.#", "###"], "9": ["###", "#.#", "###", "..#", "##."],
    " ": ["...", "...", "...", "...", "..."], ".": ["...", "...", "...", "...", ".#."],
    ",": ["...", "...", "...", ".#.", "#.."], "-": ["...", "...", "###", "...", "..."],
    ":": ["...", ".#.", "...", ".#.", "..."], "'": [".#.", ".#.", "...", "...", "..."],
    "!": [".#.", ".#.", ".#.", "...", ".#."], "?": ["##.", "..#", ".#.", "...", ".#."],
    "/": ["..#", "..#", ".#.", "#..", "#.."], "&": [".#.", "#.#", ".#.", "#.#", ".##"],
    "º": ["###", "#.#", "###", "...", "###"], "·": ["...", "...", ".#.", "...", "..."],
    "+": ["...", ".#.", "###", ".#.", "..."], "#": ["#.#", "###", "#.#", "###", "#.#"],
}


def _glyph(ch: str) -> list[str]:
    return _FONT.get(ch, _FONT["?"])


def text_width(text: str, scale: int = 1) -> int:
    if not text:
        return 0
    return sum((len(_glyph(c)[0]) + 1) * scale for c in text.upper()) - scale


def draw_text(plate: np.ndarray, text: str, x: int, y: int, ink: int, scale: int = 1) -> None:
    """Stamp pixel text into a plate array in place (clipped at edges)."""
    h, w = plate.shape
    cx = x
    for ch in text.upper():
        for gy, row in enumerate(_glyph(ch)):
            for gx, c in enumerate(row):
                if c != "#":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        px, py = cx + gx * scale + sx, y + gy * scale + sy
                        if 0 <= px < w and 0 <= py < h:
                            plate[py, px] = ink
        cx += (len(_glyph(ch)[0]) + 1) * scale


def _truncate(text: str, max_px: int) -> str:
    if text_width(text) <= max_px:
        return text
    while text and text_width(text + ".") > max_px:
        text = text[:-1]
    return text.rstrip() + "." if text else ""


def mount_geometry(size: tuple[int, int], matte: str) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) of the image window for a given matte."""
    w, h = size
    if matte == "matted":
        side = max(6, w // 32)
        return side, side, w - 2 * side, h - side - 14
    if matte == "plate":
        m = max(5, w // 30)
        return m, m, w - 2 * m, h - 2 * m
    return 0, 0, w, h


def render(
    img: Image.Image,
    size: tuple[int, int],
    settings: PlateSettings,
    title: str = "",
    edition: int | None = None,
) -> np.ndarray:
    """Full pipeline. Returns an (h, w) uint8 plate of ink indices."""
    s = settings.clamp()
    w, h = size
    x0, y0, iw, ih = mount_geometry(size, s.matte)
    fitted = fit(img, (iw, ih), s.focus_x, s.focus_y)
    l, r = separate(fitted, s)
    inner = screen(l, r, s.screen)
    plate = np.full((h, w), PAPER, dtype=np.uint8)
    plate[y0:y0 + ih, x0:x0 + iw] = inner
    if s.matte in ("plate", "matted"):
        # A 1px plate mark one pixel outside the image, like an intaglio bevel.
        px0, py0, px1, py1 = x0 - 2, y0 - 2, x0 + iw + 1, y0 + ih + 1
        plate[py0, px0:px1 + 1] = BLACK
        plate[py1, px0:px1 + 1] = BLACK
        plate[py0:py1 + 1, px0] = BLACK
        plate[py0:py1 + 1, px1] = BLACK
    if s.matte == "matted":
        ty = h - 10
        ed = f"Nº {edition:03d}" if edition is not None else ""
        ed_w = text_width(ed)
        plate_title = _truncate(title.strip(), iw - ed_w - 8)
        draw_text(plate, plate_title, x0, ty, BLACK)
        if ed:
            draw_text(plate, ed, x0 + iw - ed_w, ty, RED)
    return plate


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

def to_palette_image(plate: np.ndarray) -> Image.Image:
    """'P' mode image with indices 0/1/2, ready for inky.set_image()."""
    im = Image.fromarray(plate, mode="P")
    pal = []
    for i in (PAPER, BLACK, RED):
        pal.extend(PREVIEW_INKS[i])
    im.putpalette(pal + [0] * (768 - len(pal)))
    return im


def to_preview(plate: np.ndarray, scale: int = 1) -> Image.Image:
    """RGB proof in approximate ink colours, optionally pixel-upscaled."""
    lut = np.array([PREVIEW_INKS[PAPER], PREVIEW_INKS[BLACK], PREVIEW_INKS[RED]], dtype=np.uint8)
    im = Image.fromarray(lut[plate], mode="RGB")
    if scale > 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    return im


def ink_coverage(plate: np.ndarray) -> dict[str, float]:
    n = plate.size
    return {
        "paper": round(float((plate == PAPER).sum()) / n, 3),
        "black": round(float((plate == BLACK).sum()) / n, 3),
        "red": round(float((plate == RED).sum()) / n, 3),
    }


def welcome_card(size: tuple[int, int], url: str) -> np.ndarray:
    """First-boot card: a QR code to the control app plus a pixel-type note."""
    import qrcode  # imported lazily; only the frame needs it

    w, h = size
    plate = np.full((h, w), PAPER, dtype=np.uint8)
    qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    m = np.array(qr.get_matrix(), dtype=bool)
    n = m.shape[0]
    mod = max(1, int(min(h * 0.62, w * 0.42)) // n)
    qsize = n * mod
    qy = (h - qsize) // 2
    qx = max(8, w // 14)
    big = np.kron(m, np.ones((mod, mod), dtype=bool))
    plate[qy:qy + qsize, qx:qx + qsize][big] = BLACK
    tx = qx + qsize + max(10, w // 20)
    avail = w - tx - max(6, w // 30)
    head = "MEZZOTINT"
    hs = max(s for s in (1, 2, 3) if s == 1 or text_width(head, s) <= avail)
    bs = 2 if hs >= 2 and text_width("SCAN TO SET", 2) <= avail else 1
    lines = ["SCAN TO SET", "A THEME."]
    block = 5 * hs + 8 + 1 + 8 + len(lines) * (5 * bs + 4 * bs)
    ty = (h - block) // 2
    draw_text(plate, head, tx, ty, BLACK, hs)
    ty += 5 * hs + 8
    plate[ty:ty + max(1, hs - 1), tx:tx + text_width(head, hs)] = RED
    ty += 8 + hs
    for line in lines:
        draw_text(plate, line, tx, ty, BLACK, bs)
        ty += 9 * bs
    short = url.replace("http://", "").replace("https://", "").rstrip("/")
    draw_text(plate, _truncate(short, avail), tx, ty + 4, RED)
    return plate
