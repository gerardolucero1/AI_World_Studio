from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d

from features.curves.curve import arc_length, influence_grid, interp_profile, sample_curve, smoothstep, terrain_heights
from terrain.heightfield.heightfield import Heightfield


def find_falls(ground: np.ndarray, ds: np.ndarray, fall_grade: float, fall_min_drop: float, merge_gap: int = 2) -> list[tuple[int, int]]:
    """Runs of steep descent along the profile -> [(lip_index, base_index)]. A fall needs a total drop >= fall_min_drop."""
    steep = ((ground[:-1] - ground[1:]) / np.maximum(ds, 1e-6)) >= fall_grade
    idx = np.flatnonzero(steep)
    if not len(idx):
        return []
    runs, a, b = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - b <= merge_gap + 1:
            b = i
        else:
            runs.append((a, b)); a, b = i, i
    runs.append((a, b))
    return [(int(a), int(b) + 1) for a, b in runs if ground[a] - ground[b + 1] >= fall_min_drop]


def create_river(hf: Heightfield, path: list[tuple[float, float]], width: float = 10.0, depth: float = 3.0,
                 bank_width: float = 12.0, min_slope: float = 0.002, water_fill: float = 0.6,
                 smoothing: float = 30.0, uphill_tolerance: float = 1.0, fall_grade: float = 1.2,
                 fall_min_drop: float = 5.0, pool_depth: float = 1.8):
    """Carve a channel that flows downhill from path[0] to path[-1]. Where the course crosses a cliff (steep descent over a total drop of at
    least `fall_min_drop` m) the face is left intact, a WATERFALL is recorded and a plunge pool is carved at its foot.
    Returns (rect, report): bed/water profiles, `falls` ([{top, bottom, height, width}]), `fall_segments` and warnings."""
    pts = sample_curve(path, hf.cell_size)
    s = arc_length(pts)
    ds = np.diff(s)
    n = len(pts)
    ground = terrain_heights(hf, pts)
    falls = find_falls(ground, ds, fall_grade, fall_min_drop)
    sig = max(smoothing / hf.cell_size, 0.5)
    # profile ranges between falls (inclusive ends); the inside of a fall is a straight drop between lip and base beds
    bounds, prev = [], 0
    for a, e in falls:
        bounds.append((prev, a)); prev = e
    bounds.append((prev, n - 1))
    smooth = ground.copy()
    for lo, hi in bounds:
        if hi > lo:
            smooth[lo:hi + 1] = gaussian_filter1d(ground[lo:hi + 1], min(sig, max((hi - lo) / 3.0, 0.5)), mode="nearest")
    bed = smooth - depth
    for a, e in falls:
        bed[a + 1:e] = np.linspace(bed[a], bed[e], e - a + 1)[1:-1]
    for i in range(1, n):                                  # monotone: never higher than upstream - min_slope
        bed[i] = min(bed[i], bed[i - 1] - min_slope * ds[i - 1])
    warnings: list[dict] = []
    worst, where = 0.0, 0
    for lo, hi in bounds:
        if hi > lo:
            seg = smooth[lo:hi + 1]; r = seg - np.minimum.accumulate(seg)
            if r.max() > worst:
                worst, where = float(r.max()), lo + int(r.argmax())
    if worst > uphill_tolerance:
        warnings.append({"severity": "warning", "code": "river_uphill", "location": [float(pts[where][0]), float(pts[where][1])],
                         "message": f"Terrain rises {worst:.1f} m against the flow; channel was cut through it.",
                         "suggestion": "Lower the terrain ahead, move the river or add a lake/dam."})
    hw = width / 2
    rect, d, idx, _ = influence_grid(hf, pts, hw + bank_width)
    bed_g, d = interp_profile(hf, pts, bed, rect, idx)    # bed interpolated along the river (smooth, no stair-steps)
    cross = bed_g + depth * 0.35 * np.clip(d / hw, 0, 1) ** 2          # parabolic channel
    s_bank = smoothstep((d - hw) / max(bank_width, 1e-6))
    cur = hf.data[rect[0]:rect[1], rect[2]:rect[3]]
    edge = bed_g + depth * 0.35
    new = np.where(d <= hw, cross, edge * (1 - s_bank) + cur * s_bank)
    mask = d <= hw + bank_width
    cur[mask] = np.minimum(cur[mask], new[mask]).astype(np.float32)
    water = bed + depth * water_fill
    water = np.minimum.accumulate(water)
    fall_info = []
    c = hf.cell_size
    for a, e in falls:                                     # plunge pool at the foot of each fall
        px, pz = pts[e]; R = max(width * 1.1, 4.0)
        y0, y1, x0, x1 = hf.world_to_rect(px, pz, R)
        X, Y = np.meshgrid(np.arange(x0, x1) * c, np.arange(y0, y1) * c)
        dd = np.hypot(X - px, Y - pz) / R
        tgt = bed[e] - pool_depth * np.clip(1 - dd ** 2, 0, 1)
        sub = hf.data[y0:y1, x0:x1]
        m = dd < 1
        sub[m] = np.minimum(sub[m], tgt[m]).astype(np.float32)
        rect = (min(rect[0], y0), max(rect[1], y1), min(rect[2], x0), max(rect[3], x1))
        fall_info.append({"top": [float(pts[a][0]), float(water[a]), float(pts[a][1])], "bottom": [float(pts[e][0]), float(water[e]), float(pts[e][1])],
                          "height": float(water[a] - water[e]), "width": float(width), "lip_index": int(a), "base_index": int(e)})
    report = {"points": pts.round(3).tolist(), "bed": bed.round(3).tolist(), "water_level": water.round(3).tolist(),
              "width": width, "length": float(s[-1]), "falls": fall_info, "fall_segments": [[int(a), int(e)] for a, e in falls], "warnings": warnings}
    return rect, report
