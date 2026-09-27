"""Assembly preview: centre section + assembled views, with stand-in hardware."""
import sys
import numpy as np
import trimesh
from render import render, sheet
from fitcheck import hardware
from mezzotint_case import P

p = P()
body = trimesh.load("out/mezzotint_body.stl")
cover = trimesh.load("out/mezzotint_cover.stl")
hw = hardware(p)
PET, COV = (150, 178, 186), (205, 186, 150)
GLASS, PCB, METAL = (40, 42, 50), (60, 130, 80), (170, 170, 175)
col = lambda n: GLASS if "wHAT" in n else PCB if "PCB" in n else METAL
parts = [(body, PET), (cover, COV)] + [(m, col(n)) for n, m in hw.items()]

cx = float(np.mean(body.bounds[:, 0]))
lo, hi = np.min([m.bounds[0] for m, _ in parts], 0) - 5, np.max([m.bounds[1] for m, _ in parts], 0) + 5
half = trimesh.creation.box(bounds=[[lo[0], lo[1], lo[2]], [cx, hi[1], hi[2]]])
def cut(m):
    try:
        s = trimesh.boolean.intersection([m, half], engine="manifold")
    except Exception:
        return None
    return None if s.is_empty else s
sec = [(c, rgb, 1) for m, rgb in parts if (c := cut(m)) is not None]

tiles = [
    (render(sec, (-1, -0.12, 0.35), size=600), "section at centre: glass | booster | Pi | posts | cover"),
    (render([(body, PET, 0.35), (cover, COV, 0.35)] + [(m, c, 1) for m, c in parts[2:]],
            (0.35, -0.3, -1), size=600), "assembled from the back (shell ghosted)"),
    (render([(body, PET, 1), (cover, COV, 1), (hw["wHAT board+glass"], GLASS, 1)],
            (-0.3, -0.25, 1), size=600), "assembled from the front"),
]
sheet(tiles, sys.argv[1] if len(sys.argv) > 1 else "assembly.png")
