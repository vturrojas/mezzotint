"""Easel assembly: frame on the shelf, clash check, tipping check, renders."""
import sys
import numpy as np
import trimesh
import trimesh.proximity as tp
from build123d import export_stl
from mezzotint_case import P, Geo, easel_parts, easel_frame, EaselGeo
from fitcheck import hardware, overlap
from render import render, sheet

p = P()
g, e = Geo(p), EaselGeo(p)
loc, eu, en = easel_frame(p)


def to_mesh(part, name):
    path = f"/tmp/claude-0/easel_{name}.stl"
    export_stl(part, path, tolerance=0.05, angular_tolerance=0.2)
    return trimesh.load(path)


front, rear, shelf = easel_parts(p)
W = {n: to_mesh(loc * part, n) for n, part in (("front", front), ("rear", rear), ("shelf", shelf))}

# frame model (X, Y, Z) -> easel local (x, u, n) -> world
ex, eu, en = np.array([-1.0, 0, 0]), np.array(eu), np.array(en)
y_bot = g.cy - g.oh / 2
n0 = p.leg / 2 + p.standoff + 0.3                   # cover back face; keyhole blocks reach the legs
def frame_xform(m):
    v = m.vertices
    x = -(v[:, 0] - g.cx)
    u = p.shelf_u + 0.2 + (v[:, 1] - y_bot)
    n = n0 + (p.zb() - v[:, 2])
    out = m.copy()
    out.vertices = x[:, None] * ex + u[:, None] * eu + n[:, None] * en
    out.fix_normals()
    return out

frame = {n: frame_xform(trimesh.load(f"out/mezzotint_{n}.stl")) for n in ("body", "cover")}
hw = hardware(p)
glass = frame_xform(hw["wHAT board+glass"])
ux = p.pi_x + p.usb_x
y0 = p.pi_y + p.pi_h - 1                             # front-view Y (down)
y1 = (-y_bot) + p.plug_drop                          # outer bottom (front-view) + rigid boot
zc = p.front_t + p.pi_front_z - 1.5
plug = trimesh.creation.box(extents=(8, y1 - y0, 8))
plug.apply_translation((-ux, -(y0 + y1) / 2, zc))    # model coords (front-view x -> -X)
plug = frame_xform(plug)

ok = True
print("clashes (exact booleans):")
pairs = [("front", "rear"), ("front", "shelf"), ("rear", "shelf")]
pairs += [(f, e_) for f in ("frame body", "frame cover") for e_ in ("front", "rear", "shelf")]
pairs += [("plug", e_) for e_ in ("front", "rear", "shelf")]
M = {**W, "frame body": frame["body"], "frame cover": frame["cover"], "plug": plug}
for a, b in pairs:
    vol, _, _ = overlap(M[a], M[b])
    ok &= vol <= 0.5
    print(f"  {a:12s} vs {b:6s} {'CLASH' if vol > 0.5 else 'clear':6s} {vol:7.2f} mm3")
d = -tp.signed_distance(W["shelf"], plug.sample(4000)).max()
print(f"  plug -> shelf notch, closest: {d:.2f} mm")
d = -tp.signed_distance(W["front"], W["rear"].sample(20000)).max()
print(f"  back leg -> stop, closest: {d:.2f} mm (the stop it rests on)")

d = -tp.signed_distance(W["front"], frame["cover"].sample(6000)).max()
print(f"  frame back -> front legs, closest: {d:.2f} mm (should be ~0.3: it leans on them)")
d = -tp.signed_distance(W["shelf"], frame["body"].sample(6000)).max()
print(f"  frame bottom -> shelf, closest: {d:.2f} mm")

# tipping: centre of mass of everything vs the feet
parts = list(W.values()) + list(frame.values())
dens = {"PETG": 1.27e-3}
masses = [m.volume * 1.27e-3 * 0.35 for m in W.values()]     # ~35% fill, g/mm3
fm = 190.0                                                    # frame + wHAT + Pi, grams (estimate)
com = sum(m.center_mass * w for m, w in zip(W.values(), masses)) + frame["body"].center_mass * fm
com /= sum(masses) + fm
allv = np.vstack([m.vertices for m in W.values()])
feet = allv[allv[:, 2] < 0.5]
ymin, ymax = feet[:, 1].min(), feet[:, 1].max()
print(f"COM y={com[1]:.1f} z={com[2]:.1f}; feet span y {ymin:.1f} .. {ymax:.1f} (viewer at +y)")
print(f"  margin to tip forward {ymax - com[1]:.1f} mm, backward {com[1] - ymin:.1f} mm")
ok &= (ymax - com[1]) > 8
print("RESULT:", "PASS" if ok else "FAIL")

WOOD, PET, COV, GL = (182, 196, 202), (150, 178, 186), (205, 186, 150), (40, 42, 50)
scene = [(W["front"], WOOD, 1), (W["rear"], WOOD, 1), (W["shelf"], WOOD, 1),
         (frame["body"], PET, 1), (frame["cover"], COV, 1), (glass, GL, 1), (plug, (30, 30, 30), 1)]
tiles = [
    (render(scene, (0.45, -1, -0.35), up=(0, 0, 1), size=620), "easel: front 3/4"),
    (render(scene, (-1, 0, -0.08), up=(0, 0, 1), size=620), "easel: side (lean, back leg, stop)"),
    (render(scene, (-0.5, 1, -0.4), up=(0, 0, 1), size=620), "easel: back 3/4 (pivot, plug through the shelf)"),
]
sheet(tiles, sys.argv[1] if len(sys.argv) > 1 else "easel.png")
sys.exit(0 if ok else 1)
