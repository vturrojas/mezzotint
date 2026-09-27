"""Ray-traced preview of the STL parts (trimesh + embree, CPU only)."""
import sys
import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFont


def render(meshes, view_dir, up=(0, 1, 0), size=520, bg=(244, 242, 238)):
    """Orthographic render. meshes: list of (mesh, rgb, alpha)."""
    d = np.asarray(view_dir, float); d /= np.linalg.norm(d)
    up = np.asarray(up, float); right = np.cross(d, up); right /= np.linalg.norm(right); up = np.cross(right, d)
    allv = np.vstack([m.vertices for m, _, _ in meshes])
    c = (allv.min(0) + allv.max(0)) / 2
    r = np.linalg.norm(allv - c, axis=1).max() * 1.05
    xs = np.linspace(-r, r, size); ys = np.linspace(r, -r, size)
    X, Y = np.meshgrid(xs, ys)
    origins = c + X.reshape(-1, 1) * right + Y.reshape(-1, 1) * up - d * r * 3
    dirs = np.tile(d, (origins.shape[0], 1))
    img = np.tile(np.array(bg, float), (origins.shape[0], 1))
    depth = np.full(origins.shape[0], np.inf)
    # key light from upper-left of the camera, plus a soft headlight
    light = d * 0.7 - right * 0.45 + up * 0.55; light = light / np.linalg.norm(light)
    fill = d.copy()
    for mesh, rgb, alpha in meshes:
        locs, idx_ray, idx_tri = mesh.ray.intersects_location(origins, dirs, multiple_hits=False)
        if len(idx_ray) == 0:
            continue
        t = ((locs - origins[idx_ray]) * d).sum(1)
        closer = t < depth[idx_ray]
        idx_ray, idx_tri, t = idx_ray[closer], idx_tri[closer], t[closer]
        n = mesh.face_normals[idx_tri]
        n = np.where(((n * d).sum(1) > 0)[:, None], -n, n)
        k = 0.28 + 0.55 * np.clip(-(n @ light), 0, 1) + 0.30 * np.clip(-(n @ fill), 0, 1)
        col = np.clip(np.array(rgb)[None, :] * k[:, None], 0, 255)
        img[idx_ray] = img[idx_ray] * (1 - alpha) + col * alpha
        depth[idx_ray] = t
    return Image.fromarray(img.reshape(size, size, 3).astype(np.uint8))


def sheet(tiles, out, cols=3):
    w, h = tiles[0][0].size
    rows = (len(tiles) + cols - 1) // cols
    S = Image.new("RGB", (cols * w, rows * (h + 28)), (244, 242, 238))
    dr = ImageDraw.Draw(S)
    for i, (im, label) in enumerate(tiles):
        x, y = (i % cols) * w, (i // cols) * (h + 28)
        S.paste(im, (x, y + 28)); dr.text((x + 10, y + 8), label, fill=(40, 40, 44))
    S.save(out); print("wrote", out)


if __name__ == "__main__":
    L = lambda n: trimesh.load(f"out/mezzotint_{n}.stl")
    body, cover, cradle = L("body"), L("cover"), L("cradle")
    PET, COV, CR = (150, 178, 186), (205, 186, 150), (180, 164, 200)
    tiles = [
        (render([(body, PET, 1)], (0, 0, 1)), "body: front (looking at the window)"),
        (render([(body, PET, 1)], (0.35, -0.45, -1)), "body: rear 3/4 (lip, bosses, vents)"),
        (render([(body, PET, 1)], (0.2, -0.25, 1)), "body: back (bottom-weighted cavity, strip relief)"),
        (render([(cover, COV, 1)], (0.3, -0.4, 1)), "cover: inside (foam-dot rings)"),
        (render([(cover, COV, 1)], (0.3, -0.4, -1)), "cover: outside (keyhole blocks)"),
        (render([(cradle, CR, 1)], (0.55, -0.7, -0.5), up=(0, 0, 1)), "cradle: rear 3/4 (cable channel)"),
    ]
    sheet(tiles, sys.argv[1] if len(sys.argv) > 1 else "preview.png")
