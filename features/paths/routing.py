"""Trail routing and traversability on the heightfield (grid graph + Dijkstra, vectorized with scipy)."""
from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

from biomes.masks.masks import water_mask
from terrain.heightfield.heightfield import Heightfield

_DIRS = [(0, 1), (1, 0), (1, 1), (1, -1)]   # forward neighbours (undirected graph)


class Grid:
    """Coarse graph over the heightfield. stride trades precision for speed."""

    def __init__(self, hf: Heightfield, log=(), stride: int | None = None, blocked: np.ndarray | None = None):
        self.hf = hf
        self.stride = stride or max(1, int(round(max(hf.nx, hf.ny) / 400)))
        s = self.stride
        self.h = hf.data[::s, ::s].astype(np.float64)
        self.ny, self.nx = self.h.shape
        self.step = hf.cell_size * s
        wm = water_mask(hf, log)[::s, ::s]
        self.blocked = wm if blocked is None else (wm | blocked[::s, ::s])

    def idx(self, x: float, y: float) -> int:
        j = int(np.clip(round(x / self.step), 0, self.nx - 1)); i = int(np.clip(round(y / self.step), 0, self.ny - 1))
        return i * self.nx + j

    def xy(self, node: int) -> tuple[float, float]:
        i, j = divmod(node, self.nx)
        return j * self.step, i * self.step

    def edges(self, max_grade: float | None, grade_weight: float = 6.0, ref_grade: float = 0.35):
        """(rows, cols, weights) for allowed edges. max_grade (rise/run) None = any grade (cost still penalises steepness)."""
        R, C, W = [], [], []
        ids = np.arange(self.ny * self.nx).reshape(self.ny, self.nx)
        hflat, bflat = self.h.ravel(), self.blocked.ravel()
        for di, dj in _DIRS:
            ys = slice(0, self.ny - di)
            if dj >= 0:
                a_ids, b_ids = ids[ys, :self.nx - dj], ids[di:, dj:]
            else:
                a_ids, b_ids = ids[ys, -dj:], ids[di:, :self.nx + dj]
            a_, b_ = a_ids.ravel(), b_ids.ravel()
            dist = np.hypot(di, dj) * self.step
            grade = np.abs(hflat[b_] - hflat[a_]) / dist
            ok = ~(bflat[a_] | bflat[b_])
            if max_grade is not None:
                ok &= grade <= max_grade
            R.append(a_[ok]); C.append(b_[ok]); W.append((dist * (1.0 + (grade / ref_grade) ** 2 * grade_weight))[ok])
        return np.concatenate(R), np.concatenate(C), np.concatenate(W)

    def graph(self, max_grade, **kw):
        r, c, w = self.edges(max_grade, **kw)
        n = self.ny * self.nx
        return coo_matrix((w, (r, c)), shape=(n, n)).tocsr()


def simplify(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    ab = b - a; l2 = max(float(ab @ ab), 1e-12)
    t = np.clip(((pts - a) @ ab) / l2, 0, 1)
    d = np.hypot(*(pts - (a + t[:, None] * ab)).T)
    i = int(d.argmax())
    if d[i] > tol:
        return np.vstack([simplify(pts[:i + 1], tol)[:-1], simplify(pts[i:], tol)])
    return np.vstack([a, b])


def route(hf: Heightfield, log, start, end, waypoints=(), max_grade: float | None = None, stride: int | None = None) -> dict:
    """Cheapest route start -> (waypoints) -> end. Returns {"ok", "points", "length", "max_grade", "climb"} or an error.
    max_grade=None lets the route cross steep ground at high cost (the trail op then carves a ramp)."""
    g = Grid(hf, log, stride)
    graph = g.graph(max_grade)
    stops = [start, *waypoints, end]
    nodes: list[int] = []
    for a, b in zip(stops[:-1], stops[1:]):
        ia, ib = g.idx(*a), g.idx(*b)
        if g.blocked.ravel()[ia] or g.blocked.ravel()[ib]:
            return {"ok": False, "error": "start/end is in water or outside the allowed area"}
        dist, pred = dijkstra(graph, directed=False, indices=ia, return_predecessors=True)
        if not np.isfinite(dist[ib]):
            return {"ok": False, "error": f"no route from {a} to {b} with max_grade={max_grade}; raise max_grade or move the endpoints"}
        seg, n = [], ib
        while n != ia:
            seg.append(n); n = pred[n]
        seg.append(ia); seg.reverse()
        nodes += seg if not nodes else seg[1:]
    # loop erasure: a waypoint off the optimal line makes the route go there and come back; drop such out-and-back spurs
    seen: dict[int, int] = {}
    clean: list[int] = []
    for n in nodes:
        if n in seen:
            del clean[seen[n] + 1:]
            seen = {m: i for i, m in enumerate(clean)}
        else:
            seen[n] = len(clean); clean.append(n)
    pts = [g.xy(n) for n in clean]
    arr = np.asarray(pts)
    if len(arr) > 6:                                       # round off tight turns (a hairpin makes the carved trail overlap itself)
        from scipy.ndimage import gaussian_filter1d
        sm = np.stack([gaussian_filter1d(arr[:, 0], 2.0, mode="nearest"), gaussian_filter1d(arr[:, 1], 2.0, mode="nearest")], -1)
        sm[0], sm[-1] = arr[0], arr[-1]
        arr = sm
    simp = simplify(arr, g.step * 1.5)
    hs = np.array([hf.sample(float(x), float(y)) for x, y in arr])
    ds = np.hypot(*np.diff(arr, axis=0).T)
    grades = np.abs(np.diff(hs)) / np.maximum(ds, 1e-6)
    return {"ok": True, "points": [[float(x), float(y)] for x, y in simp], "length": float(ds.sum()),
            "max_grade": float(grades.max()) if len(grades) else 0.0, "mean_grade": float(grades.mean()) if len(grades) else 0.0,
            "climb": float(np.clip(np.diff(hs), 0, None).sum())}


def traversability(hf: Heightfield, log, start=None, max_slope_deg: float = 40.0, stride: int | None = None) -> dict:
    """What can a walker reach from `start` without crossing water or ground steeper than max_slope_deg?"""
    g = Grid(hf, log, stride)
    graph = g.graph(np.tan(np.radians(max_slope_deg)))
    n_comp, labels = connected_components(graph, directed=False)
    # spawn at the BOTTOM of the trail network (lowest trail start), where a visitor would arrive
    ends = [(op.result["elevation"][0], op.result["points"][0]) for op in log if getattr(op, "kind", None) == "path" and op.enabled and op.result]
    trail_starts = [min(ends, key=lambda t: t[0])[1]] if ends else []
    if start is None and trail_starts:                   # the world's trail network is the intended way around: start where it starts
        start = tuple(trail_starts[0])
    if start is None:                                    # flattest, most central cell INSIDE the largest walkable component
        yy, xx = np.gradient(g.h)
        flat = np.hypot(xx, yy) / g.step
        cy, cx = g.ny / 2, g.nx / 2
        score = flat + 0.0005 * np.hypot(np.arange(g.ny)[:, None] - cy, np.arange(g.nx)[None, :] - cx)
        lab2 = labels.reshape(g.ny, g.nx)
        sizes = np.bincount(labels[(~g.blocked).ravel()], minlength=n_comp)
        score[g.blocked | (lab2 != int(np.argmax(sizes)))] = 9
        i, j = np.unravel_index(score.argmin(), score.shape); start = (j * g.step, i * g.step)
    s_node = g.idx(*start)
    reach = (labels == labels[s_node]).reshape(g.ny, g.nx) & ~g.blocked
    land = ~g.blocked
    top_i, top_j = np.unravel_index(np.where(land, g.h, -1e9).argmax(), g.h.shape)
    # unreachable land components large enough to matter
    others = []
    comp_ids, counts = np.unique(labels[(~reach.ravel()) & land.ravel()], return_counts=True)
    for cid, cnt in sorted(zip(comp_ids, counts), key=lambda t: -t[1])[:5]:
        m = (labels == cid).reshape(g.ny, g.nx) & land
        if cnt * g.step ** 2 < 2500:
            continue
        ys, xs = np.where(m)
        others.append({"area_m2": float(cnt * g.step ** 2), "bbox_m": [float(xs.min() * g.step), float(ys.min() * g.step),
                                                                        float(xs.max() * g.step), float(ys.max() * g.step)],
                       "mean_height": float(g.h[m].mean())})
    return {"ok": True, "start": [float(start[0]), float(start[1])], "max_slope_deg": max_slope_deg,
            "reachable_fraction": float(reach.sum() / max(land.sum(), 1)),
            "summit": {"x": float(top_j * g.step), "y": float(top_i * g.step), "height": float(g.h[top_i, top_j]),
                       "reachable": bool(reach[top_i, top_j])},
            "unreachable_regions": others, "_mask": reach, "_stride": g.stride}


def route_river(hf: Heightfield, start, end, tolerance: float = 1.2, drop_penalty: float = 150.0, stride: int | None = None) -> dict:
    """Course for a river from start (upstream) to end: it descends (small climbs of up to `tolerance` m per step are allowed but costly, and the
    river op then cuts through them), and prefers to cross cliffs where the drop is smallest (waterfalls are costly, so it looks for the lowest riser).
    Water cells are NOT blocked (rivers end in lakes)."""
    g = Grid(hf, (), stride)
    g.blocked = np.zeros_like(g.blocked)
    ids = np.arange(g.ny * g.nx).reshape(g.ny, g.nx)
    hflat = g.h.ravel()
    R, C, W = [], [], []
    for di, dj in _DIRS:
        if dj >= 0:
            a_ids, b_ids = ids[:g.ny - di, :g.nx - dj], ids[di:, dj:]
        else:
            a_ids, b_ids = ids[:g.ny - di, -dj:], ids[di:, :g.nx + dj]
        a_, b_ = a_ids.ravel(), b_ids.ravel()
        dist = np.hypot(di, dj) * g.step
        dh = hflat[a_] - hflat[b_]                      # >0 = a is higher than b
        for src, dst, drop in ((a_, b_, dh), (b_, a_, -dh)):
            ok = drop >= -tolerance
            grade = np.maximum(drop, 0) / dist                  # steepness of the descent: cliffs are expensive, so the river looks for the lowest riser
            cost = dist * (1.0 + 0.02 * np.maximum(drop, 0)) + drop_penalty * dist * np.maximum(grade - 0.6, 0) ** 2 + 12.0 * np.maximum(-drop, 0)   # climbing is penalised
            R.append(src[ok]); C.append(dst[ok]); W.append(cost[ok])
    n = g.ny * g.nx
    graph = coo_matrix((np.concatenate(W), (np.concatenate(R), np.concatenate(C))), shape=(n, n)).tocsr()
    ia, ib = g.idx(*start), g.idx(*end)
    dist, pred = dijkstra(graph, directed=True, indices=ia, return_predecessors=True)
    if not np.isfinite(dist[ib]):
        return {"ok": False, "error": "no downhill course from start to end (the end is higher than everything reachable); move the start uphill or the end downhill"}
    seg, k = [], ib
    while k != ia:
        seg.append(k); k = pred[k]
    seg.append(ia); seg.reverse()
    arr = np.array([g.xy(k) for k in seg])
    if len(arr) > 6:
        from scipy.ndimage import gaussian_filter1d
        sm = np.stack([gaussian_filter1d(arr[:, 0], 1.5, mode="nearest"), gaussian_filter1d(arr[:, 1], 1.5, mode="nearest")], -1)
        sm[0], sm[-1] = arr[0], arr[-1]; arr = sm
    simp = simplify(arr, g.step * 1.2)
    hs = np.array([hf.sample(float(x), float(y)) for x, y in arr])
    return {"ok": True, "points": [[float(x), float(y)] for x, y in simp], "length": float(np.hypot(*np.diff(arr, axis=0).T).sum()),
            "drop": float(hs[0] - hs[-1]), "max_step_drop": float(np.max(hs[:-1] - hs[1:])) if len(hs) > 1 else 0.0}
