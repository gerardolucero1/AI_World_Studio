"""Common curve system for rivers/paths: Catmull-Rom through control points, resampled by arc length."""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from terrain.heightfield.heightfield import Heightfield, Rect


def sample_curve(points: list[tuple[float, float]], spacing: float, closed: bool = False) -> np.ndarray:
    """Return (N,2) points ~evenly spaced by `spacing` along a Catmull-Rom spline through `points`."""
    p = np.asarray(points, np.float64)
    if len(p) < 2:
        raise ValueError("curve needs >= 2 points")
    if closed:
        ctrl = np.vstack([p[-1], p, p[0], p[1]])
    else:
        ctrl = np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]])
    t = np.linspace(0, 1, 24, endpoint=False)[:, None]
    segs = []
    for i in range(len(ctrl) - 3):
        p0, p1, p2, p3 = ctrl[i:i + 4]
        segs.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t ** 2
                           + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    dense = np.vstack(segs + [p[:1] if closed else p[-1:]])
    seg_len = np.hypot(*np.diff(dense, axis=0).T)
    s = np.concatenate([[0], np.cumsum(seg_len)])
    n = max(int(s[-1] / spacing) + 1, 2)
    u = np.linspace(0, s[-1], n)
    return np.stack([np.interp(u, s, dense[:, 0]), np.interp(u, s, dense[:, 1])], -1)


def arc_length(pts: np.ndarray) -> np.ndarray:
    return np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])


def terrain_heights(hf: Heightfield, pts: np.ndarray) -> np.ndarray:
    return np.array([hf.sample(float(x), float(y)) for x, y in pts], np.float32)


def influence_grid(hf: Heightfield, pts: np.ndarray, radius: float):
    """For every grid cell within `radius` of the curve: (rect, dist, nearest_sample_index, in_mask)."""
    c = hf.cell_size
    x0 = max(int((pts[:, 0].min() - radius) / c), 0); x1 = min(int((pts[:, 0].max() + radius) / c) + 2, hf.nx)
    y0 = max(int((pts[:, 1].min() - radius) / c), 0); y1 = min(int((pts[:, 1].max() + radius) / c) + 2, hf.ny)
    X, Y = np.meshgrid(np.arange(x0, x1) * c, np.arange(y0, y1) * c)
    d, idx = cKDTree(pts).query(np.stack([X.ravel(), Y.ravel()], -1))
    return (y0, y1, x0, x1), d.reshape(X.shape).astype(np.float32), idx.reshape(X.shape), None


def smoothstep(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0, 1)
    return t * t * (3 - 2 * t)


def interp_profile(hf: Heightfield, pts: np.ndarray, values: np.ndarray, rect: Rect, idx: np.ndarray):
    """For every grid cell of `rect`: distance to the polyline and the profile value interpolated ALONG it (no stair-steps between samples).
    `idx` = nearest sample index per cell (from influence_grid)."""
    y0, y1, x0, x1 = rect
    c = hf.cell_size
    X, Y = np.meshgrid(np.arange(x0, x1) * c, np.arange(y0, y1) * c)
    n = len(pts)
    best_d = np.full(X.shape, np.inf); best_v = np.zeros(X.shape)
    for off in (-1, 0):                                   # the two segments touching the nearest sample
        a = np.clip(idx + off, 0, n - 2); b = a + 1
        A, B = pts[a], pts[b]; AB = B - A
        l2 = np.maximum((AB ** 2).sum(-1), 1e-12)
        t = np.clip(((X - A[..., 0]) * AB[..., 0] + (Y - A[..., 1]) * AB[..., 1]) / l2, 0, 1)
        d = np.hypot(X - (A[..., 0] + t * AB[..., 0]), Y - (A[..., 1] + t * AB[..., 1]))
        v = values[a] * (1 - t) + values[b] * t
        m = d < best_d
        best_d = np.where(m, d, best_d); best_v = np.where(m, v, best_v)
    return best_v.astype(np.float32), best_d.astype(np.float32)
