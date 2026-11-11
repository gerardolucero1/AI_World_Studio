"""Editing existing curves (river/trail paths, lake outlines): a few handles per curve, and dragging one moves the nearby points
with a smooth falloff along the curve, so a densely routed path stays smooth instead of getting a kink."""
from __future__ import annotations

import numpy as np

EDITABLE = {"river": ("path", False), "path": ("path", False), "lake": ("polygon", True)}     # op kind -> (param key, closed)
MAX_HANDLES = 12


def _arc(pts: np.ndarray, closed: bool) -> np.ndarray:
    p = np.vstack([pts, pts[:1]]) if closed else pts
    return np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))])


def handle_indices(points, closed: bool = False, max_handles: int = MAX_HANDLES) -> list[int]:
    """Indices of the points shown as handles: every point if there are few, else evenly spaced by arc length (ends included)."""
    pts = np.asarray(points, np.float64)
    n = len(pts)
    if n <= max_handles:
        return list(range(n))
    s = _arc(pts, closed)[:n]
    targets = np.linspace(0, s[-1], max_handles)
    idx = sorted({int(np.argmin(np.abs(s - t))) for t in targets} | ({0, n - 1} if not closed else {0}))
    return idx


def drag_points(points, index: int, new_xy, closed: bool = False, influence: float | None = None) -> list[list[float]]:
    """Move point `index` to new_xy; points within `influence` metres of it ALONG the curve follow with a smooth falloff.
    influence None = half the spacing between handles (so neighbouring handles stay put)."""
    pts = np.asarray(points, np.float64).copy()
    n = len(pts)
    delta = np.asarray(new_xy, np.float64) - pts[index]
    s = _arc(pts, closed)
    total = s[-1]
    s = s[:n]
    if influence is None:
        hi = handle_indices(pts, closed)
        influence = (total / max(len(hi) - 1, 1)) if len(hi) < n else 0.0
    d = np.abs(s - s[index])
    if closed:
        d = np.minimum(d, total - d)
    if influence <= 0:
        w = (np.arange(n) == index).astype(np.float64)
    else:
        t = np.clip(d / influence, 0, 1)
        w = 1 - t * t * (3 - 2 * t)                      # smoothstep falloff: 1 at the handle, 0 at `influence`
    pts += w[:, None] * delta[None, :]
    return [[round(float(x), 3), round(float(y), 3)] for x, y in pts]
