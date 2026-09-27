"""Virtual assembly: stand-in hardware vs. printed parts, collision-checked."""
import sys
import numpy as np
import trimesh
sys.path.insert(0, ".")
from mezzotint_case import P, Geo


def box(x0, x1, y0, y1, z0, z1):
    """Front-view x/y (y down) + depth z -> mesh in model coords (y up)."""
    m = trimesh.creation.box(extents=(x1 - x0, y1 - y0, z1 - z0))
    m.apply_translation(((x0 + x1) / 2, -(y0 + y1) / 2, (z0 + z1) / 2))
    return m


def cyl(x, y, r, z0, z1):
    m = trimesh.creation.cylinder(radius=r, height=z1 - z0, sections=32)
    m.apply_translation((x, -y, (z0 + z1) / 2))
    return m


def hardware(p: P):
    """Stand-ins for a Pi 3 A+ under an Inky wHAT, from the measured layout."""
    f = p.front_t
    wb = f + p.board_t                  # back of the wHAT
    pz = f + p.pi_front_z               # Pi component face
    pb = pz + p.pi_t                    # Pi underside (the Pi's back)
    X, Y = p.pi_x, p.pi_y
    ux = X + p.usb_x
    hw = {
        "wHAT board+glass": box(0, p.board_w, 0, p.board_h, f, wb),
        "GPIO stack": box(X + 7, X + 58, Y + 1, Y + 6, wb, pz),
        "standoffs": None,
        "Pi PCB": box(X, X + p.pi_w, Y, Y + p.pi_h, pz, pb),
        "USB-A port": box(X + p.pi_w - 14, X + p.pi_w + p.jack_overhang, Y + 16, Y + 31, pz - 7, pz),
        "HDMI port": box(X + 24.5, X + 39.5, Y + p.pi_h - 11, Y + p.pi_h + 1, pz - 6.5, pz),
        "audio jack": box(X + 50, X + 57, Y + p.pi_h - 12, Y + p.pi_h + 1, pz - 6, pz),
        "micro-USB + 90deg plug": box(ux - 5, ux + 16, Y + p.pi_h, Y + p.pi_h + 11, pz - 5.5, pz + 2.5),
        "solder tails": box(X + 7, X + 58, Y + 1, Y + 6, pb, pb + 2.0),
        "microSD + card": box(X - p.sd_overhang, X + 12, Y + 21, Y + 33, pb, pb + 2.0),
        "wHAT breakout header": box(62, 88.5, 66, 76.5, wb, wb + 8.5),
    }
    del hw["standoffs"]
    for i, (hx, hy) in enumerate(p.pi_holes):
        hw[f"standoff {i+1}"] = cyl(X + hx, Y + hy, 2.75, wb, pz)
        hw[f"screw head {i+1}"] = cyl(X + hx, Y + hy, 2.4, pb, pb + p.screw_head_h)
    return hw


def overlap(a, b):
    """Exact intersection: (volume mm3, z-thickness mm, bbox) via manifold booleans."""
    try:
        i = trimesh.boolean.intersection([a, b], engine="manifold")
    except Exception:
        return 0.0, 0.0, None
    if i.is_empty or i.volume < 1e-3:
        return 0.0, 0.0, None
    ext = i.bounds[1] - i.bounds[0]
    return i.volume, float(min(ext)), i.bounds


def main():
    p = P()
    parts = {n: trimesh.load(f"out/mezzotint_{n}.stl") for n in ("body", "cover")}
    hw = hardware(p)
    ok = True
    print("interference (exact booleans); posts stop pad_gap short of the screw heads")
    for pn, pm in parts.items():
        for hn, hm in hw.items():
            vol, thick, _ = overlap(pm, hm)
            if vol > 0:
                ok = False
            tag = "CLASH" if vol > 0 else "clear"
            print(f"  {pn:5s} vs {hn:20s} {tag:16s} vol {vol:7.2f} mm3  thickness {thick:.2f} mm")
    vol, thick, _ = overlap(parts["body"], parts["cover"])
    print(f"  body  vs cover (engaged)          {'CLASH' if vol > 0 else 'clear':16s} vol {vol:7.2f} mm3")
    ok &= vol == 0
    g = Geo(p)
    # the tightest gaps that matter
    import trimesh.proximity as tp
    for pn, hn in (("cover", "screw head 1"), ("cover", "solder tails"), ("cover", "microSD + card"),
                   ("body", "wHAT breakout header"), ("body", "micro-USB + 90deg plug"), ("body", "microSD + card")):
        d = -tp.signed_distance(parts[pn], hw[hn].sample(4000)).max()
        print(f"  min gap {pn} -> {hn:22s} {d:5.2f} mm")
    print(f"cavity {g.cw:.1f} x {g.ch:.1f} x {p.depth():.1f} mm; wHAT side margin {min(g.cw - p.board_w, g.ch - p.board_h) / 2:.2f} mm")
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok, hw


if __name__ == "__main__":
    sys.exit(0 if main()[0] else 1)
