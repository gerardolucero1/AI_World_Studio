from __future__ import annotations

from terrain.heightfield.heightfield import Heightfield


def pick(hf: Heightfield, origin, direction, max_dist: float = 20000.0):
    """Ray-march against the heightfield. Returns (x, height, z) in world coords, or None.
    Axes: X right, Y up (height), Z = grid row."""
    sx, sz = hf.size
    step = hf.cell_size * 0.75
    t, t_prev, prev_above = 0.0, 0.0, None
    while t < max_dist:
        p = origin + direction * t
        if 0 <= p[0] <= sx and 0 <= p[2] <= sz:
            above = p[1] > hf.sample(float(p[0]), float(p[2]))
            if prev_above and not above:
                lo, hi = t_prev, t
                for _ in range(12):
                    mid = (lo + hi) / 2
                    q = origin + direction * mid
                    if q[1] > hf.sample(float(q[0]), float(q[2])):
                        lo = mid
                    else:
                        hi = mid
                q = origin + direction * hi
                return float(q[0]), hf.sample(float(q[0]), float(q[2])), float(q[2])
            prev_above, t_prev = above, t
        else:
            prev_above = None
        t += step * max(1.0, t / 400.0)
    return None
