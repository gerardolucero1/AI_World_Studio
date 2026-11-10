"""Headless software renderer for agent inspection: top-down map and perspective views (numpy + Pillow)."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

from biomes.masks.masks import water_mask
from materials.materials import COLORS, final_weights
from terrain.heightfield.heightfield import Heightfield

LIGHT = np.array([0.4, 0.8, 0.3], np.float32) / np.linalg.norm([0.4, 0.8, 0.3])


def _surface(hf: Heightfield, log):
    """Per-cell albedo (ny,nx,3) incl. water, and per-cell unit normals (ny,nx,3)."""
    w = final_weights(hf, log)
    albedo = w @ COLORS
    wm = water_mask(hf, log)
    albedo[wm] = albedo[wm] * 0.3 + np.array([0.1, 0.3, 0.6], np.float32) * 0.7
    gy, gx = np.gradient(hf.data, hf.cell_size)
    n = np.stack([-gx, np.ones_like(gx), -gy], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return albedo.astype(np.float32), n.astype(np.float32)


def _shade(albedo, n):
    d = np.clip(n @ LIGHT, 0, 1)
    return np.clip(albedo * (0.4 + 0.18 * n[..., 1:2] + 0.7 * d[..., None]), 0, 1)


def _veg_overlay(img, hf, log, scale_x, scale_y):
    for op in log:
        if op.result and "_instances" in op.result:
            inst = op.result["_instances"]
            px = np.clip((inst[:, 0] * scale_x).astype(int), 0, img.shape[1] - 1)
            py = np.clip((inst[:, 2] * scale_y).astype(int), 0, img.shape[0] - 1)
            img[py, px] = img[py, px] * 0.4 + np.array([0.05, 0.25, 0.08]) * 0.6


def top_down(hf: Heightfield, log, size: int = 512, vegetation: bool = True) -> Image.Image:
    albedo, n = _surface(hf, log)
    img = _shade(albedo, n)
    if vegetation:
        _veg_overlay(img, hf, log, (hf.nx - 1) / hf.size[0], (hf.ny - 1) / hf.size[1])
    im = Image.fromarray((img * 255).astype(np.uint8))
    return im.resize((size, int(size * hf.ny / hf.nx)), Image.BILINEAR)


def perspective(hf: Heightfield, log, pos, yaw: float, pitch: float, fov: float = 60.0, width: int = 480, height: int = 300,
                max_dist: float | None = None, steps: int = 400) -> Image.Image:
    """Ray-marched view. pos=(x, height_above_ground_or_absolute y, z): pos[1] is ABSOLUTE height (m)."""
    albedo, n = _surface(hf, log)
    shaded = _shade(albedo, n)
    sx, sz = hf.size
    max_dist = max_dist or float(np.hypot(sx, sz))
    yw, pt = np.radians(yaw), np.radians(pitch)
    fwd = np.array([np.cos(pt) * np.cos(yw), np.sin(pt), np.cos(pt) * np.sin(yw)])
    right = np.cross(fwd, [0, 1, 0]); right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    aspect = width / height; th = np.tan(np.radians(fov) / 2)
    u = (np.arange(width) + 0.5) / width * 2 - 1; v = 1 - (np.arange(height) + 0.5) / height * 2
    U, V = np.meshgrid(u * th * aspect, v * th)
    d = fwd[None, None] + U[..., None] * right + V[..., None] * up
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    o = np.array(pos, np.float64)
    t = np.full((height, width), 0.5)
    hit = np.zeros((height, width), bool)
    c = hf.cell_size
    step0 = c * 0.8
    for _ in range(steps):
        p = o + d * t[..., None]
        px, pz = p[..., 0] / c, p[..., 2] / c
        inside = (px >= 0) & (px <= hf.nx - 1.001) & (pz >= 0) & (pz <= hf.ny - 1.001)
        ix, iz = np.clip(px.astype(int), 0, hf.nx - 2), np.clip(pz.astype(int), 0, hf.ny - 2)
        fx, fz = np.clip(px - ix, 0, 1), np.clip(pz - iz, 0, 1)
        h = (hf.data[iz, ix] * (1 - fx) * (1 - fz) + hf.data[iz, ix + 1] * fx * (1 - fz)
             + hf.data[iz + 1, ix] * (1 - fx) * fz + hf.data[iz + 1, ix + 1] * fx * fz)
        below = inside & (p[..., 1] <= h) & ~hit
        hit |= below
        active = ~hit & (t < max_dist)
        if not active.any():
            break
        t = np.where(active, t + np.maximum(step0, (p[..., 1] - h) * 0.4 * inside + step0 * ~inside + t * 0.004), t)
    zmap = np.where(hit, t * (d @ fwd), np.inf)          # view-space depth of terrain per pixel
    p = o + d * t[..., None]
    ix = np.clip((p[..., 0] / c).astype(int), 0, hf.nx - 1); iz = np.clip((p[..., 2] / c).astype(int), 0, hf.ny - 1)
    sky_t = np.clip(0.5 + 0.5 * d[..., 1], 0, 1)[..., None]
    sky = (1 - sky_t) * np.array([0.8, 0.88, 0.95]) + sky_t * np.array([0.35, 0.6, 0.9])
    col = shaded[iz, ix]
    fog = np.clip(t / max_dist, 0, 1)[..., None] ** 1.5
    col = col * (1 - fog) + np.array([0.75, 0.82, 0.9]) * fog
    out = np.where(hit[..., None], col, sky)
    cam = dict(o=o, fwd=fwd, right=right, up=up, th=th, aspect=aspect, w=width, h=height, max_dist=max_dist)
    for pts, cols, size_m in _scene_points(hf, log):
        _splat(out, zmap, pts, cols, size_m, cam)
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))


def _scene_points(hf, log):
    """Yield (points Nx3, colors Nx3, world size in m) for independent geometry and vegetation."""
    rock = np.array([0.58, 0.52, 0.45], np.float32)
    veg_cols = np.array([(.10, .35, .12), (.25, .5, .15), (.5, .48, .45), (.7, .55, .2)], np.float32)
    for op in log:
        r = op.result
        if not (op.enabled and r):
            continue
        if "_mesh" in r:
            m = r["_mesh"]; v = m.vertices
            fc = v[m.faces].mean(1)
            fn = m.face_normals(); fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-9)
            vn = m.vertex_normals()
            pts = np.vstack([v, fc]); nrm = np.vstack([vn, fn])
            light = np.clip(nrm @ LIGHT, 0, 1)
            shade = (0.45 + 0.12 * nrm[:, 1] + 0.7 * light)[:, None]
            yield pts, np.clip(rock * shade, 0, 1), hf.cell_size * 1.6
        if "_instances" in r:
            inst = r["_instances"]
            if not len(inst):
                continue
            t = np.linspace(0.05, 1.0, 10)
            for k in t:   # a vertical column of points per tree, narrowing to the tip
                pts = np.stack([inst[:, 0], inst[:, 1] + inst[:, 5] * k, inst[:, 2]], -1)
                base = veg_cols[inst[:, 6].astype(int) % len(veg_cols)]
                yield pts, np.clip(base * (0.55 + 0.45 * k), 0, 1), np.maximum(inst[:, 4] * 2 * (1.05 - k), 0.5)


def _splat(img, zmap, pts, cols, size_m, cam):
    rel = pts - cam["o"]
    z = rel @ cam["fwd"]
    ok = z > 0.5
    rel, z, cols = rel[ok], z[ok], cols[ok]
    size = np.broadcast_to(np.asarray(size_m, np.float64), pts.shape[:1])[ok]
    foc = cam["h"] / 2 / cam["th"]
    x = (rel @ cam["right"]) / (z * cam["th"] * cam["aspect"]); y = (rel @ cam["up"]) / (z * cam["th"])
    px = ((x + 1) / 2 * cam["w"]).astype(int); py = ((1 - y) / 2 * cam["h"]).astype(int)
    rad = np.clip((size * foc / z / 2).astype(int), 0, 5)
    keep = (px >= -4) & (px < cam["w"] + 4) & (py >= -4) & (py < cam["h"] + 4) & (z < cam["max_dist"])
    px, py, z, cols, rad = px[keep], py[keep], z[keep], cols[keep], rad[keep]
    order = np.argsort(-z)                                  # far first so near overwrites
    px, py, z, cols, rad = px[order], py[order], z[order], cols[order], rad[order]
    rmax = int(rad.max()) if len(rad) else 0
    for dy in range(-rmax, rmax + 1):
        for dx in range(-rmax, rmax + 1):
            m = (np.abs(dx) <= rad) & (np.abs(dy) <= rad)
            xx, yy = px[m] + dx, py[m] + dy
            v = (xx >= 0) & (xx < cam["w"]) & (yy >= 0) & (yy < cam["h"])
            xx, yy, zz, cc = xx[v], yy[v], z[m][v], cols[m][v]
            vis = zz < zmap[yy, xx] + 0.5
            img[yy[vis], xx[vis]] = cc[vis]
            zmap[yy[vis], xx[vis]] = zz[vis]


def png_bytes(im: Image.Image) -> bytes:
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()
