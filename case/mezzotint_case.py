"""Mezzotint case: a slim-bezel enclosure for a Pimoroni Inky wHAT on a Pi 3 A+.

Parts (all parametric, build123d):

  body    Front bezel and walls in one piece, printed face-down so the front
          is the smooth bed side. The window has a 45-degree mat-cut bevel.
  cover   Back plate, inset flush. Its top edge slides up under a lip in the
          body; two countersunk M3 screws at the bottom lock it. Four foam
          dots, placed in shallow rings, press on the Pi's mounting screws and
          clamp the stack to the bezel without loading the display glass.
          Prints inside-face down with no supports.
          Two keyhole blocks outside hang it on the wall, 4.5 mm proud.
  easel   A desk easel in homage to portrayt's: two splayed front legs, a
          shelf the frame stands on, and a back leg that rises between the
          leg tops on an M3 pivot and folds flat. A stop behind the front legs
          sets its angle. The power plug drops through a notch in the shelf.
  fitring The first few mm of the body: a 20-minute print to check that the
          wHAT drops in and the window lines up before the long print.

Inputs use "front view" coordinates: X right from the wHAT's left edge, Y DOWN
from its top edge, Z back from the glass. Hold it display-toward-you with the
ribbon strip at the bottom when measuring.

Run:  python mezzotint_case.py [params.json]  ->  ./out/*.step, *.stl
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from build123d import (
    Align, Axis, Box, BuildPart, Cone, Cylinder, Location, Locations, Mode, Part, Plane, Pos, Rot,
    Vector,
    chamfer, export_step, export_stl, fillet,
)

MIN = (Align.CENTER, Align.CENTER, Align.MIN)
MAX = (Align.CENTER, Align.CENTER, Align.MAX)
MIN_C = MIN


@dataclass
class P:
    # Inky wHAT, measured: 90 x 77 mm. Active area is the panel's 400 x 300
    # px at 0.212 mm pitch; its position was scaled off a photo of a print
    # whose plate mark sits at known pixels (+-0.4 mm).
    board_w: float = 90.0
    board_h: float = 77.0
    board_t: float = 3.4
    active_w: float = 84.8
    active_h: float = 63.6
    active_left: float = 2.6        # board left edge -> image left edge
    active_top: float = 2.3         # board top edge  -> image top edge
    fpc_extra: float = 0.5          # extra room at the bottom edge where the ribbon folds round
    strip_relief: float = 0.8       # pocket in the bezel's back over the bottom glass strip

    # Raspberry Pi 3 A+ (FCC ID 2ABCB-RPI3AP), component side facing the
    # display. Seen from the front: GPIO at the top, microSD at the left edge
    # (on the Pi's back), micro-USB/HDMI/audio along the bottom, USB-A on the right.
    pi_w: float = 65.0
    pi_h: float = 57.0
    pi_t: float = 1.6
    pi_x: float = 4.0               # wHAT left edge -> Pi left edge (SD side)
    pi_y: float = 6.0               # wHAT top edge  -> Pi top edge
    pi_front_z: float = 18.4        # glass front -> Pi component face (20 mm to the Pi's back)
    pi_under: float = 2.5           # solder tails, SD slot and card on the Pi's back
    jack_overhang: float = 3.0      # USB-A past the Pi's right edge
    sd_overhang: float = 4.0        # card tip is flush with the wHAT's edge
    sd_clear: float = 0.6
    usb_x: float = 12.5             # micro-USB centre from the Pi's left edge (drawing says
                                    # 10.6, a photo of the cord says 14.4; cutouts cover both)
    usb_slot: float = 16.0          # width of the cord cutouts
    pi_holes: list = field(default_factory=lambda: [[3.5, 3.5], [61.5, 3.5], [3.5, 52.5], [61.5, 52.5]])
    screw_head_h: float = 2.2

    # case
    wall: float = 2.4
    front_t: float = 2.2            # thick enough to frost evenly in clear PETG
    fit: float = 0.35               # PETG clearance per side
    pi_clear: float = 1.8
    back_clear: float = 0.8
    win_overlap: float = 0.5        # the mat covers this much of the image edge, hiding tolerance
    pad_ring: float = 3.4           # locating ring for a 6 mm foam dot over each Pi screw
    bevel: float = 1.2
    corner_r: float = 3.0
    cover_t: float = 2.4
    lip: float = 1.6
    slide: float = 2.4              # > lip, so the cover drops in past the lip
    boss: float = 7.0
    screw_pilot: float = 2.5        # M3 self-tapping into PETG
    screw_clear: float = 3.4
    csk_d: float = 6.4              # M3 countersunk (ISO 10642) head, 90 degrees
    keyhole_head: float = 8.6       # #6 / 4 mm wall screw
    keyhole_shank: float = 4.4
    standoff: float = 4.5
    tilt_deg: float = 15.0
    # "bottom": straight micro-USB plug through a slot in the bottom wall, open
    # to the back so the stack drops in with the plug fitted (use the easel or
    # hang it). "rear": right-angle cable out of a notch in the cover.
    cable_exit: str = "bottom"
    plug_drop: float = 12.0         # rigid plug boot below the frame's bottom edge

    # easel (local frame: x right, u up the front legs, n toward the viewer)
    lean_deg: float = 20.0          # front legs lean back from vertical
    leg: float = 10.0               # square stock
    leg_chamfer: float = 1.6
    easel_h: float = 150.0          # front leg length
    foot_x: float = 55.0            # front feet, centre to leg centre
    rear_deg: float = 42.0          # back leg opens this far from the front legs
    rear_rise: float = 11.0         # back leg rises above the pivot, like the original
    shelf_u: float = 45.0           # shelf top, along the legs
    shelf_len: float = 118.0
    shelf_t: float = 9.0
    lip: float = 3.0
    cable_d: float = 4.2

    # ---- derived ----
    def window(self):
        o = self.win_overlap
        return (self.active_left + o, self.active_top + o, self.active_w - 2 * o, self.active_h - 2 * o)

    def cavity(self):
        """Interior footprint (x0, y0, x1, y1), front view. Centred on the
        window left-right so the side bezels match; top and bottom follow the
        board, which gives a bottom-weighted mat over the ribbon strip."""
        f, c = self.fit, self.pi_clear
        xs = [-f, self.board_w + f, self.pi_x - self.sd_overhang - self.sd_clear,
              self.pi_x + self.pi_w + self.jack_overhang + c]
        ys = [-f, self.board_h + f + self.fpc_extra, self.pi_y - c, self.pi_y + self.pi_h + c]
        wx, wy, ww, wh = self.window()
        cx = wx + ww / 2
        hw = max(cx - min(xs), max(xs) - cx)
        return cx - hw, min(ys), cx + hw, max(ys)

    def depth(self):
        """Glass front face -> inside face of the cover."""
        return self.pi_front_z + self.pi_t + self.pi_under + self.back_clear

    def zb(self):
        """Back face of the body (from the front face)."""
        return self.front_t + self.depth() + self.cover_t


def B(x, y):
    """Front-view coords (X right, Y down, seen from the front) -> model.
    The viewer looks at the glass (z = 0) from -Z, so their right is model -X."""
    return -x, -y


def rounded_box(w, h, d, r):
    b = Box(w, h, d, align=MIN)
    return fillet(b.edges().filter_by(Axis.Z), r)


class Geo:
    """Shared positions so body and cover always agree."""

    def __init__(self, p: P):
        x0, y0, x1, y1 = p.cavity()
        self.cw, self.ch = x1 - x0, y1 - y0
        self.cx, self.cy = B((x0 + x1) / 2, (y0 + y1) / 2)
        self.top_in = self.cy + self.ch / 2       # model Y of cavity top
        self.bot_in = self.cy - self.ch / 2
        self.ow, self.oh = self.cw + 2 * p.wall, self.ch + 2 * p.wall
        # engaged cover: inner half reaches up under the lip; bottom leaves the slide gap
        self.cov_top = self.top_in - p.fit
        self.cov_bot = self.bot_in + p.fit + p.slide
        self.bosses = [(self.cx + s * (self.cw / 2 - p.boss / 2), self.cov_bot + p.boss / 2) for s in (-1, 1)]
        self.usb_x = B(p.pi_x + p.usb_x, 0)[0]


def body(p: P) -> Part:
    g = Geo(p)
    zb = p.zb()
    wx, wy, ww, wh = p.window()
    wcx, wcy = B(wx + ww / 2, wy + wh / 2)

    with BuildPart() as bp:
        with Locations((g.cx, g.cy, 0)):
            rounded_box(g.ow, g.oh, zb, p.corner_r + p.wall)
        with Locations((g.cx, g.cy, p.front_t)):
            Box(g.cw, g.ch, zb, align=MIN, mode=Mode.SUBTRACT)
        with Locations((wcx, wcy, 0)):
            Box(ww, wh, p.front_t, align=MIN, mode=Mode.SUBTRACT)

        # mat-cut bevel on the window's front edges
        front = bp.edges().filter_by_position(Axis.Z, -0.01, 0.01).filter_by(Axis.Z, reverse=True)
        win = [e for e in front if abs(e.center().X - wcx) <= ww / 2 + 0.01
               and abs(e.center().Y - wcy) <= wh / 2 + 0.01]
        chamfer(win, p.bevel)

        # shallow pocket behind the non-image strip at the bottom of the glass,
        # so the ribbon's white cover strip can't rock the panel
        if p.strip_relief > 0:
            sy0 = wy + wh + p.win_overlap + 1.2
            sy1 = p.board_h + p.fit + p.fpc_extra
            with Locations((g.cx, B(0, (sy0 + sy1) / 2)[1], p.front_t - p.strip_relief)):
                Box(g.cw - 2.0, sy1 - sy0, p.strip_relief + 0.01, align=MIN, mode=Mode.SUBTRACT)

        # lip across the top, level with the cover's outer half
        with Locations((g.cx, g.top_in - p.lip / 2, zb - p.cover_t / 2)):
            Box(g.cw, p.lip, p.cover_t / 2, align=MIN)

        # straight-cable option: micro-USB window in the bottom wall
        if p.cable_exit == "bottom":
            z0 = p.front_t + p.pi_front_z - 1.5 - 5.5      # plug boot is ~8 mm round
            with Locations((g.usb_x, g.bot_in - p.wall / 2, z0)):
                Box(p.usb_slot, p.wall + 2, zb - z0 + 1, align=MIN, mode=Mode.SUBTRACT)

        # vent slots in the top and bottom walls, in the Pi's depth band
        vz0 = p.front_t + p.pi_front_z - 10
        for i in range(-4, 5):
            vx = g.cx + i * 6.5
            for vy, skip_usb in ((g.top_in + p.wall / 2, False), (g.bot_in - p.wall / 2, True)):
                if skip_usb and p.cable_exit == "bottom" and abs(vx - g.usb_x) < p.usb_slot / 2 + 4:
                    continue
                if skip_usb and any(abs(vx - bx) < p.boss for bx, _ in g.bosses):
                    continue
                with Locations((vx, vy, vz0)):
                    Box(2.2, p.wall + 2, 12.0, align=MIN, mode=Mode.SUBTRACT)
    return bp.part + bosses(p)


def bosses(p: P) -> Part:
    """Cover-screw bosses in the bottom corners. Their undersides slope up at
    45 degrees from the side walls, so the body prints face-down without
    supports, and they stay clear of the header on the back of the wHAT."""
    g = Geo(p)
    h, z1 = 10.0, p.zb() - p.cover_t
    out = None
    for side, (bx, by) in zip((-1, 1), g.bosses):
        y_lo, y_hi = g.bot_in - 0.5, by + p.boss / 2   # 0.5 into the bottom wall to fuse
        yc = (y_lo + y_hi) / 2
        wallx = g.cx + side * g.cw / 2
        blk = Pos(bx + side * 0.25, yc, z1 - h) * Box(p.boss + 0.5, y_hi - y_lo, h, align=MIN)
        cut = Pos(wallx, yc, z1 - h) * Rot(0, side * 45, 0) * Box(60, 40, 60, align=MAX)
        blk = blk - cut - Pos(bx, by, z1 - h) * Cylinder(p.screw_pilot / 2, h, align=MIN)
        out = blk if out is None else out + blk
    return out


def cover(p: P) -> Part:
    """Modelled in place (engaged position), inner face at z = zb - cover_t."""
    g = Geo(p)
    zi = p.zb() - p.cover_t
    cw = g.cw - 2 * p.fit
    ch = g.cov_top - g.cov_bot
    ccy = (g.cov_top + g.cov_bot) / 2

    with BuildPart() as bp:
        with Locations((g.cx, ccy, zi)):
            rounded_box(cw, ch, p.cover_t, max(0.5, p.corner_r - p.fit))
        # the outer half steps back so it sits below the lip
        with Locations((g.cx, g.cov_top - (p.lip + p.fit) / 2, zi + p.cover_t / 2 - 0.1)):
            Box(cw + 1, p.lip + p.fit + 0.05, p.cover_t / 2 + 0.2, align=MIN, mode=Mode.SUBTRACT)

        # 90-degree countersunk holes over the bosses
        csk_h = (p.csk_d - p.screw_clear) / 2
        for bx, by in g.bosses:
            with Locations((bx, by, zi)):
                Cylinder(p.screw_clear / 2, p.cover_t, align=MIN, mode=Mode.SUBTRACT)
            with Locations((bx, by, zi + p.cover_t - csk_h)):
                Cone(p.screw_clear / 2, p.csk_d / 2, csk_h + 0.01, align=MIN, mode=Mode.SUBTRACT)

        # rear cable exit: a U-notch in the cover's bottom edge, centred
        if p.cable_exit == "rear":
            nw = p.cable_d + 1.2
            with Locations((g.cx, g.cov_bot + (nw + 1.0) / 2 - 0.5, zi)):
                Box(nw, nw + 1.0, p.cover_t, align=MIN, mode=Mode.SUBTRACT)
            with Locations((g.cx, g.cov_bot + nw + 0.5, zi)):
                Cylinder(nw / 2, p.cover_t, align=MIN, mode=Mode.SUBTRACT)

        # locating rings for the foam dots that press on the Pi's screw heads
        for hx, hy in p.pi_holes:
            px, py = B(p.pi_x + hx, p.pi_y + hy)
            with Locations((px, py, zi)):
                Cylinder(p.pad_ring, 0.3, align=MIN, mode=Mode.SUBTRACT)
                Cylinder(p.pad_ring - 0.5, 0.3, align=MIN, mode=Mode.ADD)

        # keyhole blocks near the top, on the outside
        ko = p.zb()
        kb_w, kb_h, skin = 16.0, 26.0, 1.8
        for s in (-1, 1):
            kx, ky = g.cx + s * cw * 0.3, g.cov_top - kb_h / 2 - 5
            with Locations((kx, ky, ko)):
                rounded_box(kb_w, kb_h, p.standoff, 3.0)
            # channel for the screw head, behind the wall-facing skin
            with Locations((kx, ky + 2.5, ko)):
                Box(p.keyhole_head, 15.0, p.standoff - skin, align=MIN, mode=Mode.SUBTRACT)
            # entry hole (low) and shank slot running up, through the skin
            face = ko + p.standoff - skin
            with Locations((kx, ky - 4.0, face)):
                Cylinder(p.keyhole_head / 2, skin, align=MIN, mode=Mode.SUBTRACT)
            with Locations((kx, ky + 2.5, face)):
                Box(p.keyhole_shank, 11.0, skin, align=MIN, mode=Mode.SUBTRACT)
    return bp.part


# ---------------------------------------------------------------- easel

def easel_frame(p: P):
    """Location mapping easel-local (x, u, n) to world (z up, viewer on +Y)."""
    L = math.radians(p.lean_deg)
    eu = (0, -math.sin(L), math.cos(L))
    en = (0, math.cos(L), math.sin(L))
    # x is the viewer's right (world -X: they stand on +Y looking toward -Y), so
    # x, u, n is right-handed
    return Location(Plane(origin=(0, 0, 0), x_dir=(-1, 0, 0), z_dir=en)), eu, en


def _bar(p0, p1, w, t, ch, ydir):
    """Square bar from p0 to p1 (easel-local), every edge chamfered."""
    v0, v1 = Vector(*p0), Vector(*p1)
    z = (v1 - v0).normalized()
    y = Vector(*ydir)
    y = (y - z * y.dot(z)).normalized()
    x = y.cross(z)
    b = chamfer(Box(w, t, (v1 - v0).length, align=MIN_C).edges(), ch)
    return Location(Plane(origin=v0, x_dir=x, z_dir=z)) * b


class EaselGeo:
    def __init__(self, p: P):
        self.gap = p.leg + 0.8                                  # back leg + clearance
        s = 0.0
        for _ in range(5):                                      # leg-top centre vs splay
            tx = self.gap / 2 + (p.leg / 2) / math.cos(s)
            s = math.atan((p.foot_x - tx) / p.easel_h)
        self.tx, self.splay = tx, s
        self.pivot_u = p.easel_h - 7.0
        a = math.radians(p.rear_deg)
        self.d = (0.0, -math.cos(a), -math.sin(a))              # back leg, pointing down
        self.ledge_u0 = p.shelf_u - p.shelf_t

    def leg_x(self, p: P, u):
        return p.foot_x - (p.foot_x - self.tx) * u / p.easel_h


def _ground_cut(part, loc):
    world = loc * part
    world = world & Box(1000, 1000, 1000, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return loc.inverse() * world


def easel_rear_local(p: P, grow: float = 0.0) -> Part:
    e = EaselGeo(p)
    d = Vector(*e.d)
    pv = Vector(0, e.pivot_u, 0)
    top = pv - d * p.rear_rise
    bot = pv + d * (e.pivot_u * 1.2)
    w = p.leg + 2 * grow
    return _bar(bot, top - d * grow, w, w, p.leg_chamfer, (0, 0, 1))


def easel_parts(p: P):
    """Return (front, rear, ledge) in easel-local coordinates, ground-cut."""
    e = EaselGeo(p)
    loc, _, _ = easel_frame(p)
    t = p.leg

    legs = None
    for s in (-1, 1):
        b = _bar((s * (p.foot_x + 15 * math.tan(e.splay)), -15, 0), (s * e.tx, p.easel_h, 0),
                 t, t, p.leg_chamfer, (0, 0, 1))
        legs = b if legs is None else legs + b

    # stop: a block behind the leg tops, cut to the open back leg's shape
    u0, u1 = e.pivot_u - 17, e.pivot_u - 3
    wx = e.leg_x(p, u0)                                  # reaches the middle of each leg
    stop = Pos(0, u0, -t / 2 - 15) * Box(2 * wx, u1 - u0, 15, align=(Align.CENTER, Align.MIN, Align.MIN))
    stop = chamfer(stop.edges().filter_by(Axis.Y), 1.2)
    front = legs + stop
    front = front - easel_rear_local(p, grow=0.3)

    # pivot hole, and holes for the shelf screws
    pv = (0, e.pivot_u, 0)
    hole = Pos(*pv) * Rot(0, 90, 0) * Cylinder(1.7, 80)
    front = front - hole
    rear = easel_rear_local(p) - hole
    ux = e.ledge_u0 + p.shelf_t / 2
    for s in (-1, 1):
        lx = s * e.leg_x(p, ux)
        front = front - Pos(lx, ux, 0) * Cylinder(1.7, 40)
        front = front - Pos(lx, ux, -t / 2 - 0.01) * Cone(3.2, 1.7, 1.6, align=MIN)   # countersink, back face

    # shelf: back face on the front of the legs, lip at the front
    depth = 34.0
    n0 = t / 2
    shelf = Pos(0, e.ledge_u0, n0) * Box(p.shelf_len, p.shelf_t, depth + p.lip,
                                         align=(Align.CENTER, Align.MIN, Align.MIN))
    shelf = shelf + Pos(0, e.ledge_u0, n0 + depth) * Box(p.shelf_len, p.shelf_t + 4, p.lip,
                                                        align=(Align.CENTER, Align.MIN, Align.MIN))
    # cord notch, open to the back, where the plug comes out of the frame
    cx = -(Geo(p).usb_x - Geo(p).cx)                 # model -> easel x (viewer's right is +x)
    shelf = shelf - Pos(cx, e.ledge_u0 - 1, n0 - 1) * Box(p.usb_slot, p.shelf_t + 2, 22,
                                                          align=(Align.CENTER, Align.MIN, Align.MIN))
    for s in (-1, 1):
        shelf = shelf - Pos(s * e.leg_x(p, ux), ux, n0 - 0.01) * Cylinder(1.25, 13, align=MIN)

    front = _ground_cut(front, loc)
    rear = _ground_cut(rear, loc)
    return front, rear, shelf


def easel_print(p: P):
    """Each easel part laid flat for printing, no supports."""
    front, rear, shelf = easel_parts(p)
    front_p = Rot(180, 0, 0) * front                        # front faces on the bed
    rear_p = Rot(0, 90, 0) * rear                           # on its side
    shelf_p = Rot(90, 0, 0) * shelf                         # underside down, lip up
    out = {}
    for name, part in (("easel_front", front_p), ("easel_back_leg", rear_p), ("easel_shelf", shelf_p)):
        bb = part.bounding_box()
        out[name] = Pos(-bb.center().X, -bb.center().Y, -bb.min.Z) * part
    return out


def fitring(p: P) -> Part:
    """Front plate plus the first few mm of wall: proves the fit in minutes."""
    h = p.front_t + p.board_t + 1.5
    g = Geo(p)
    return body(p) & Pos(g.cx, g.cy, 0) * Box(g.ow + 2, g.oh + 2, h, align=MIN)


def build(p: P):
    return {"body": body(p), "cover": cover(p), "fitring": fitring(p), **easel_print(p)}


def main(params_file: str | None = None):
    p = P()
    if params_file:
        p = P(**{**asdict(p), **json.loads(Path(params_file).read_text())})
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    parts = build(p)
    for name, part in parts.items():
        export_step(part, str(out / f"mezzotint_{name}.step"))
        export_stl(part, str(out / f"mezzotint_{name}.stl"), tolerance=0.02, angular_tolerance=0.1)
        s = part.bounding_box().size
        print(f"{name:7s} {s.X:6.1f} x {s.Y:6.1f} x {s.Z:5.1f} mm   {part.volume / 1000:5.1f} cm3   valid={part.is_valid}")
    g = Geo(p)
    print(f"frame {g.ow:.1f} x {g.oh:.1f} x {p.zb():.1f} mm (+{p.standoff} keyhole standoff)")
    return p, parts


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
