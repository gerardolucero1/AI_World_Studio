from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d

from features.curves.curve import arc_length, influence_grid, interp_profile, sample_curve, smoothstep, terrain_heights
from terrain.heightfield.heightfield import Heightfield


def create_path(hf: Heightfield, path: list[tuple[float, float]], width: float = 4.0, max_slope: float = 0.25,
                bank_width: float = 6.0, smoothing: float = 20.0, max_cut_fill: float = 3.0):
    """Flatten a trail into the terrain with a limited longitudinal slope (rise/run).
    Reports segments where the grade needed more cut/fill than `max_cut_fill` (suggest zigzags/ramps/steps)."""
    pts = sample_curve(path, hf.cell_size)
    s = arc_length(pts); ds = np.diff(s)
    ground = terrain_heights(hf, pts)
    h = gaussian_filter1d(ground, max(smoothing / hf.cell_size, 0.5), mode="nearest")
    orig_slope = float(np.max(np.abs(np.diff(h) / ds)))
    for _ in range(3):                                   # clamp grade forward/backward until stable
        for i in range(1, len(h)):
            lim = max_slope * ds[i - 1]; h[i] = np.clip(h[i], h[i - 1] - lim, h[i - 1] + lim)
        for i in range(len(h) - 2, -1, -1):
            lim = max_slope * ds[i]; h[i] = np.clip(h[i], h[i + 1] - lim, h[i + 1] + lim)
    warnings = []
    dev = np.abs(h - ground)
    if dev.max() > max_cut_fill:
        i = int(dev.argmax())
        warnings.append({"severity": "warning", "code": "path_too_steep", "location": [float(pts[i][0]), float(pts[i][1])],
                         "message": f"Needs {dev.max():.1f} m of cut/fill to keep grade <= {max_slope:.0%} (terrain grade {orig_slope:.0%}).",
                         "suggestion": "Add zigzags (switchbacks), a ramp, or steps; or reroute along contours."})
    hw = width / 2
    rect, d, idx, _ = influence_grid(hf, pts, hw + bank_width)
    cur = hf.data[rect[0]:rect[1], rect[2]:rect[3]]
    hh, d = interp_profile(hf, pts, h, rect, idx)         # profile interpolated along the trail: a smooth ramp, not stair-steps
    w = 1 - smoothstep((d - hw) / max(bank_width, 1e-6))
    mask = d <= hw + bank_width
    cur[mask] = (cur[mask] + (hh[mask] - cur[mask]) * w[mask]).astype(np.float32)
    return rect, {"points": pts.round(3).tolist(), "elevation": h.round(3).tolist(), "width": width,
                  "length": float(s[-1]), "max_grade": float(np.max(np.abs(np.diff(h) / ds))), "warnings": warnings}
