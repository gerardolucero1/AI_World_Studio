"""Parametric landforms. All add to / blend into the heightfield and return the dirty Rect."""
from __future__ import annotations

import numpy as np

from terrain.heightfield.heightfield import Heightfield, Rect
from terrain.procedural.noise import fbm
from terrain.sculpt.brushes import falloff


def _grid(hf: Heightfield, rect: Rect):
    y0, y1, x0, x1 = rect
    c = hf.cell_size
    return np.meshgrid(np.arange(x0, x1) * c, np.arange(y0, y1) * c)


def apply_noise(hf: Heightfield, seed: int, amplitude: float, base_freq: float = 1 / 300.0,
                octaves: int = 6, ridged: bool = False, mean_offset: float = 0.0) -> Rect:
    hf.data += (fbm(hf.nx, hf.ny, hf.cell_size, seed, base_freq, octaves, ridged=ridged) * amplitude
                + mean_offset).astype(np.float32)
    return 0, hf.ny, 0, hf.nx


def mountain(hf: Heightfield, x: float, y: float, radius: float, height: float,
             profile: float = 1.5, roughness: float = 0.15, seed: int = 0) -> Rect:
    """Radial mountain. profile>1 => sharper peak. roughness: fraction of height as ridged noise."""
    rect = hf.world_to_rect(x, y, radius)
    X, Y = _grid(hf, rect)
    t = np.clip(np.hypot(X - x, Y - y) / radius, 0, 1)
    shape = (1 - t * t * (3 - 2 * t)) ** profile
    if roughness > 0:
        n = fbm(hf.nx, hf.ny, hf.cell_size, seed, 1 / (radius * 0.6), 6, ridged=True)[rect[0]:rect[1], rect[2]:rect[3]]
        shape = shape * (1 + roughness * n)
    hf.data[rect[0]:rect[1], rect[2]:rect[3]] += (shape * height).astype(np.float32)
    return rect


def _poly_sdf(X, Y, pts: np.ndarray) -> np.ndarray:
    """Signed distance to closed polygon (negative inside). Vectorized over the grid."""
    n = len(pts)
    d2 = np.full(X.shape, np.inf, np.float32)
    inside = np.zeros(X.shape, bool)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        ab = b - a
        l2 = max(float(ab @ ab), 1e-12)
        t = np.clip(((X - a[0]) * ab[0] + (Y - a[1]) * ab[1]) / l2, 0, 1)
        d2 = np.minimum(d2, (X - a[0] - t * ab[0]) ** 2 + (Y - a[1] - t * ab[1]) ** 2)
        cond = (a[1] > Y) != (b[1] > Y)
        xint = a[0] + (Y - a[1]) * ab[0] / (ab[1] if ab[1] != 0 else 1e-12)
        inside ^= cond & (X < xint)
    d = np.sqrt(d2)
    return np.where(inside, -d, d)


def _poly_rect(hf: Heightfield, pts: np.ndarray, margin: float) -> Rect:
    c = hf.cell_size
    x0 = max(int((pts[:, 0].min() - margin) / c), 0); x1 = min(int((pts[:, 0].max() + margin) / c) + 2, hf.nx)
    y0 = max(int((pts[:, 1].min() - margin) / c), 0); y1 = min(int((pts[:, 1].max() + margin) / c) + 2, hf.ny)
    return y0, y1, x0, x1


def plateau(hf: Heightfield, polygon: list[tuple[float, float]], height: float,
            wall_width: float = 20.0, wall_noise: float = 0.0, seed: int = 0) -> Rect:
    """Blend terrain inside polygon up to absolute `height` with a steep ramp of `wall_width` m.
    Phase 4 adds real vertical wall geometry; this gives the heightfield top and a steep slope."""
    pts = np.asarray(polygon, np.float32)
    rect = _poly_rect(hf, pts, wall_width * 1.5)
    X, Y = _grid(hf, rect)
    sdf = _poly_sdf(X, Y, pts)
    if wall_noise > 0:
        n = fbm(hf.nx, hf.ny, hf.cell_size, seed, 1 / 60.0, 4)[rect[0]:rect[1], rect[2]:rect[3]]
        sdf = sdf + n * wall_noise * wall_width
    t = np.clip(1 - sdf / wall_width, 0, 1)  # 1 inside, 0 at wall_width outside
    w = (t * t * (3 - 2 * t)).astype(np.float32)
    sl = hf.data[rect[0]:rect[1], rect[2]:rect[3]]
    sl += (np.float32(height) - sl) * w
    return rect


def basin(hf: Heightfield, x: float, y: float, radius: float, depth: float, flat: float = 0.4) -> Rect:
    """Circular depression (lake bed / crater): flat floor then smooth rim."""
    rect = hf.world_to_rect(x, y, radius)
    X, Y = _grid(hf, rect)
    w = falloff(np.hypot(X - x, Y - y), radius, flat)
    hf.data[rect[0]:rect[1], rect[2]:rect[3]] -= w * depth
    return rect


def valley(hf: Heightfield, path: list[tuple[float, float]], width: float, depth: float) -> Rect:
    """Carve a smooth valley along a polyline."""
    pts = np.asarray(path, np.float32)
    rect = _poly_rect(hf, pts, width)
    X, Y = _grid(hf, rect)
    d = np.full(X.shape, np.inf, np.float32)
    for a, b in zip(pts[:-1], pts[1:]):
        ab = b - a; l2 = max(float(ab @ ab), 1e-12)
        t = np.clip(((X - a[0]) * ab[0] + (Y - a[1]) * ab[1]) / l2, 0, 1)
        d = np.minimum(d, np.hypot(X - a[0] - t * ab[0], Y - a[1] - t * ab[1]))
    hf.data[rect[0]:rect[1], rect[2]:rect[3]] -= falloff(d, width, 0.2) * depth
    return rect


def mountain_range(hf: Heightfield, path: list[tuple[float, float]], width: float, height: float,
                   seed: int = 0, roughness: float = 0.3) -> Rect:
    pts = np.asarray(path, np.float32)
    rect = _poly_rect(hf, pts, width)
    X, Y = _grid(hf, rect)
    d = np.full(X.shape, np.inf, np.float32)
    for a, b in zip(pts[:-1], pts[1:]):
        ab = b - a; l2 = max(float(ab @ ab), 1e-12)
        t = np.clip(((X - a[0]) * ab[0] + (Y - a[1]) * ab[1]) / l2, 0, 1)
        d = np.minimum(d, np.hypot(X - a[0] - t * ab[0], Y - a[1] - t * ab[1]))
    shape = falloff(d, width, 0.0) ** 1.3
    n = fbm(hf.nx, hf.ny, hf.cell_size, seed, 1 / (width * 0.8), 6, ridged=True)[rect[0]:rect[1], rect[2]:rect[3]]
    hf.data[rect[0]:rect[1], rect[2]:rect[3]] += (shape * height * (1 + roughness * n)).astype(np.float32)
    return rect


def _radial_mask(hf: Heightfield, mask, rect: Rect) -> np.ndarray | float:
    """mask = [cx, cy, r0, r1] in meters: 0 inside r0, smooth to 1 at r1 (and beyond)."""
    if not mask:
        return 1.0
    cx, cy, r0, r1 = mask
    X, Y = _grid(hf, rect)
    t = np.clip((np.hypot(X - cx, Y - cy) - r0) / max(r1 - r0, 1e-6), 0, 1)
    return (t * t * (3 - 2 * t)).astype(np.float32)


def bowl(hf: Heightfield, x: float, y: float, r_inner: float, r_outer: float, height: float,
         warp: float = 0.0, seed: int = 0, power: float = 1.0) -> Rect:
    """Raise the terrain OUTSIDE r_inner up to `height` at r_outer and beyond (a basin ringed by highlands).
    `warp` (m) distorts the radius with noise so the rim is organic."""
    rect = (0, hf.ny, 0, hf.nx)
    X, Y = _grid(hf, rect)
    d = np.hypot(X - x, Y - y)
    if warp > 0:
        d = d + fbm(hf.nx, hf.ny, hf.cell_size, seed, 1 / max(r_outer * 0.9, 50.0), 4) * warp
    t = np.clip((d - r_inner) / max(r_outer - r_inner, 1e-6), 0, 1)
    hf.data += ((t * t * (3 - 2 * t)) ** power * height).astype(np.float32)
    return rect


def terrace(hf: Heightfield, step: float = 14.0, riser: float = 0.14, warp: float = 6.0, warp_freq: float = 1 / 140.0,
            seed: int = 0, strength: float = 1.0, radial_mask: list | None = None, min_height: float | None = None) -> Rect:
    """Turn slopes into flat benches (meadows) separated by steep risers (cliff faces).
    step: bench-to-bench height (m). riser: fraction of each step that is the steep part (smaller = steeper cliffs).
    warp: meters of noise added before terracing so contour lines are irregular. radial_mask limits the effect
    ([cx, cy, r_inner, r_outer]: untouched inside r_inner). min_height: only terrace above this height."""
    rect = (0, hf.ny, 0, hf.nx)
    h = hf.data.astype(np.float64)
    n = fbm(hf.nx, hf.ny, hf.cell_size, seed, warp_freq, 4) * warp if warp > 0 else 0.0
    base = min_height if min_height is not None else float(h.min())
    u = (h - base + n) / step
    k = np.floor(u); f = u - k
    ramp = np.clip((f - (1 - riser)) / max(riser, 1e-3), 0, 1)
    ramp = ramp * ramp * (3 - 2 * ramp)
    terr = base + (k + ramp) * step
    m = _radial_mask(hf, radial_mask, rect) * strength
    if min_height is not None:
        m = m * np.clip((h - min_height) / (step * 0.5), 0, 1)
    hf.data[:] = (h + (terr - h) * m).astype(np.float32)
    return rect


def border_mountains(hf: Heightfield, width: float, height: float, roughness: float = 0.6, seed: int = 0,
                     sides: str = "nesw", power: float = 1.35) -> Rect:
    """Ring of mountains along the world edges (hides the map border, frames the playable area).
    width: how far inward the range reaches (m). Corners are rounded (superellipse distance) and the rim is noise-warped.
    sides: any of n,e,s,w (only those edges get mountains)."""
    rect = (0, hf.ny, 0, hf.nx)
    X, Y = _grid(hf, rect)
    sx, sz = hf.size
    cx, cz = sx / 2, sz / 2
    warp = fbm(hf.nx, hf.ny, hf.cell_size, seed + 7, 1 / (width * 1.6), 4) * width * 0.25
    ux = np.abs(X - cx) / (sx / 2); uz = np.abs(Y - cz) / (sz / 2)
    if "w" not in sides and "e" not in sides: ux = ux * 0
    if "n" not in sides and "s" not in sides: uz = uz * 0
    if sides and "w" in sides and "e" not in sides: ux = np.where(X < cx, ux, 0)
    if sides and "e" in sides and "w" not in sides: ux = np.where(X > cx, ux, 0)
    if sides and "n" in sides and "s" not in sides: uz = np.where(Y < cz, uz, 0)
    if sides and "s" in sides and "n" not in sides: uz = np.where(Y > cz, uz, 0)
    r = (ux ** 6 + uz ** 6) ** (1 / 6)                        # 0 at centre .. 1 at the edge, rounded corners
    d = (1 - r) * min(sx, sz) / 2 + warp                      # approx metres to the edge
    t = np.clip(1 - d / width, 0, 1)
    shape = (t * t * (3 - 2 * t)) ** power
    ridged = fbm(hf.nx, hf.ny, hf.cell_size, seed, 1 / (width * 0.9), 6, ridged=True) * 0.5 + 0.5
    broad = fbm(hf.nx, hf.ny, hf.cell_size, seed + 1, 1 / (width * 2.5), 3) * 0.5 + 0.5
    hf.data += (shape * height * (0.55 + 0.9 * roughness * ridged + 0.4 * broad * (1 - roughness))).astype(np.float32)
    return rect


def cap(hf: Heightfield, x: float, y: float, radius: float, height: float, feather: float = 8.0) -> Rect:
    """Cut everything above `height` inside `radius` down to `height` (flat summit/mesa top); never raises terrain.
    The cut fades out over `feather` metres beyond `radius`."""
    rect = hf.world_to_rect(x, y, radius + feather)
    X, Y = _grid(hf, rect)
    d = np.hypot(X - x, Y - y)
    t = np.clip((d - radius) / max(feather, 1e-6), 0, 1)
    w = 1 - t * t * (3 - 2 * t)
    sl = hf.data[rect[0]:rect[1], rect[2]:rect[3]]
    sl -= (np.maximum(sl - np.float32(height), 0) * w).astype(np.float32)
    return rect
