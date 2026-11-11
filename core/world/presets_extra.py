"""More complete landscapes (each one call; the seed varies positions and shapes):
  mesa_valley   open valley floor among stepped grey-walled mesas, tall mountains behind, ponds, a landmark tree, a waterfall off a mesa
  river_valley  rolling meadows and hills, a big river meandering from the northern range to the southern edge, an oxbow lake, forests
  canyon        a high tableland cut by a winding stepped canyon with a river at the bottom, a side gorge, buttes, a trail down into it"""
from __future__ import annotations

import math

import numpy as np

from core.world import service as S


def _blob(cx, cy, r, rng, n=26, jitter=0.28, sx=1.0, sy=1.0) -> list[list[float]]:
    k = rng.normal(0, 1, 3)
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = r * (1 + jitter * (0.5 * math.sin(3 * a + k[0]) + 0.3 * math.sin(5 * a + k[1]) + 0.2 * math.sin(9 * a + k[2])) + rng.uniform(-0.04, 0.04))
        pts.append([round(cx + rr * math.cos(a) * sx, 1), round(cy + rr * math.sin(a) * sy, 1)])
    return pts


def _meander(p0, p1, rng, amp: float, waves: float, n: int = 40) -> list[list[float]]:
    """A wandering polyline from p0 to p1 (river courses, canyons)."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = p1 - p0; L = np.linalg.norm(d); t_ = d / L; nrm = np.array([-t_[1], t_[0]])
    ph = rng.uniform(0, 2 * math.pi, 2)
    out = []
    for t in np.linspace(0, 1, n):
        env = math.sin(math.pi * t) ** 0.6                            # ends stay where asked
        off = amp * env * (math.sin(2 * math.pi * waves * t + ph[0]) + 0.35 * math.sin(2 * math.pi * waves * 2.3 * t + ph[1]))
        out.append([round(float(v), 1) for v in p0 + d * t + nrm * off])
    return out


class _Log:
    def __init__(self):
        self.steps: list[dict] = []

    def __call__(self, name, r, optional: bool = False):
        e = {"step": name, "ok": bool(r.get("ok", False)), "warnings": [w.get("code") for w in r.get("warnings", [])]}
        if not e["ok"]:
            e["error"] = r.get("error")
            if optional:
                e.update(ok=True, skipped=True)          # nice to have: the world is fine without it
        self.steps.append(e)
        return r


def _retry(log: _Log, name: str, attempts, optional: bool = False):
    """Run `attempts` (callables) until one succeeds; failed routing attempts apply nothing."""
    r = {}
    for i, f in enumerate(attempts, 1):
        r = f()
        if r.get("ok"):
            break
    log(name, r, optional)
    log.steps[-1]["attempts"] = i
    return r


def _finish(preset: str, size_m: float, res: int, seed: int, log: _Log, start, note: str) -> dict:
    t = S.analyze_traversability(start=list(start), max_slope_deg=35)
    return {"ok": all(s["ok"] for s in log.steps), "preset": preset, "size_m": size_m, "resolution": res, "seed": seed, "steps": log.steps,
            "player_start": [float(start[0]), float(start[1])],
            "traversability": {"reachable_fraction": t.get("reachable_fraction"), "unreachable_regions": len(t.get("unreachable_regions", [])),
                               "note": note}}


def _dry_point(candidates, max_slope_deg: float = 20.0):
    """First candidate (x, y) that is on dry, walkable ground (players spawn and trails start there)."""
    from biomes.masks.masks import slope_degrees, water_mask
    hf, log = S.SESSION.hf, S.SESSION.op_log
    wm, sl = water_mask(hf, log), slope_degrees(hf)
    for x, y in candidates:
        i, j = int(np.clip(y / hf.cell_size, 0, hf.ny - 1)), int(np.clip(x / hf.cell_size, 0, hf.nx - 1))
        if not wm[max(i - 3, 0):i + 4, max(j - 3, 0):j + 4].any() and sl[i, j] <= max_slope_deg:
            return (float(x), float(y))
    return tuple(map(float, candidates[0]))


def _res(size_m: float) -> int:
    return int(size_m / 2) // 4 * 4 + 1                                 # ~2 m cells, (res-1) divisible by 4


# ------------------------------------------------------------------------------------------------ mesa valley
def mesa_valley(size_m: float = 1024.0, seed: int = 1, trees: bool = True) -> dict:
    """Genshin-style: a green valley floor among stepped mesas with sheer grey walls and pines on top, tall rocky mountains behind,
    a long cliff on one side, ponds with a landmark tree, and a waterfall pouring off a mesa."""
    rng = np.random.default_rng(seed); s = size_m; res = _res(s); log = _Log()
    flip = 1 if rng.random() < 0.5 else -1                               # which side gets the long cliff
    X = lambda f: s * (0.5 + flip * (f - 0.5))                          # mirror x positions
    log("world", S.create_world("Mesa valley", s, res))
    log("relief", S.apply_terrain_noise(seed=seed, amplitude=s * 0.004, feature_size_m=s * 0.25, octaves=5))
    for i, (fx, fy, fr, fh) in enumerate([(0.84, 0.05, 0.32, 0.29), (0.99, 0.22, 0.24, 0.2), (0.58, 0.0, 0.22, 0.17), (0.25, 0.02, 0.2, 0.13)]):
        log(f"mountain_{i}", S.create_mountain(X(fx + rng.uniform(-0.04, 0.04)), s * fy, s * fr, s * fh * rng.uniform(0.9, 1.1),
                                                profile=2.2, roughness=0.3, seed=seed + 10 + i))
    side = [[X(0.0), s * 0.12], [X(0.17), s * 0.10], [X(0.25), s * 0.22], [X(0.24), s * 0.33], [X(0.29), s * 0.43], [X(0.23), s * 0.53],
            [X(0.15), s * 0.59], [X(0.0), s * 0.61]]
    log("long_cliff", S.create_plateau(side, s * 0.05, wall_width=5))
    mesas = [(0.70, 0.55, 0.16, 1.3, 0.8, 0.022), (0.78, 0.41, 0.15, 1.2, 0.8, 0.041), (0.87, 0.29, 0.12, 1.0, 1.0, 0.063),
             (0.51, 0.32, 0.11, 1.3, 0.7, 0.030), (0.59, 0.23, 0.09, 1.0, 1.0, 0.047)]
    for i, (fx, fy, fr, sx_, sy_, fh) in enumerate(mesas):
        log(f"mesa_{i}", S.create_plateau(_blob(X(fx + rng.uniform(-0.03, 0.03)), s * (fy + rng.uniform(-0.03, 0.03)), s * fr, rng, sx=sx_, sy=sy_),
                                          s * fh * rng.uniform(0.9, 1.15), wall_width=5))
    log("detail", S.apply_terrain_noise(seed=seed + 1, amplitude=s * 0.0016, feature_size_m=60, octaves=4))
    knoll = (X(0.43), s * 0.91)
    log("knoll", S.create_mountain(knoll[0], knoll[1], s * 0.18, s * 0.02, profile=1.0, roughness=0.0, seed=seed + 2))
    log("border", S.create_border_mountains(width=s * 0.1, height=s * 0.07, roughness=0.6, seed=seed + 3, sides="s"))
    ponds = [(X(0.32), s * 0.64, s * 0.03), (X(0.46), s * 0.68, s * 0.022)]
    for i, (px, py, pr) in enumerate(ponds):
        log(f"pond_{i}", S.create_lake(_blob(px, py, pr, rng, n=14, jitter=0.2), depth=2.5))
    # a waterfall: from the top of the long cliff down to the first pond
    _retry(log, "waterfall_river", [lambda fy=fy: S.create_river_auto([X(0.12), s * fy], [ponds[0][0], ponds[0][1]], width=6, depth=1.6)
                                   for fy in (0.45, 0.38, 0.52, 0.30)], optional=True)
    _retry(log, "trail", [lambda sl=sl: S.create_trail([knoll[0], knoll[1] - s * 0.02], [X(0.66), s * 0.55], width=6, max_slope=sl)
                          for sl in (0.28, 0.34, 0.4)], optional=True)
    log("style", S.set_world_style(snow_line=s * 0.2))
    if trees:
        tree = (X(0.39), s * 0.60)
        log("landmark_tree", S.place_objects([{"kind": "oak", "x": tree[0], "y": tree[1], "scale": 3.0, "yaw": float(rng.uniform(0, 360))}]))
        log("vegetation", S.scatter_vegetation(species=[
            dict(name="oak", min_sep=26, density=0.35, max_slope=18, radius=4.6, height=9.5, clusters=0.9, altitude=(-5, s * 0.015),
                 water_dist=(6, 1e9), scale=(0.8, 1.4)),
            dict(name="pine", min_sep=14, density=0.55, max_slope=24, radius=2.2, height=12, clusters=0.8, altitude=(s * 0.017, s * 0.14),
                 cliff_dist=(4, 1e9), scale=(0.8, 1.3)),
            dict(name="shrub", min_sep=9, density=0.25, max_slope=25, radius=1.2, height=1.4, clusters=0.7, altitude=(-5, s * 0.12), scale=(0.8, 1.4)),
            dict(name="rock", min_sep=22, density=0.25, min_slope=8, max_slope=45, radius=1.4, height=1.2, altitude=(-5, s * 0.09), scale=(0.7, 2.0)),
            dict(name="reed", min_sep=4, density=0.8, max_slope=15, radius=0.6, height=1.4, water_dist=(1, 8))],
            seed=seed + 4, exclusion=[_blob(knoll[0], knoll[1], s * 0.08, rng)]))
    return _finish("mesa_valley", s, res, seed, log, (knoll[0], knoll[1] - s * 0.01),
                   "mesa tops are reached by the trail or by climbing; the surrounding mountains frame the map")


# ------------------------------------------------------------------------------------------------ river valley
def river_valley(size_m: float = 1536.0, seed: int = 1, trees: bool = True) -> dict:
    """Open rolling meadows and wooded hills; a big river meanders from the northern range to the southern edge, with an oxbow lake;
    low ranges frame east and west; a riverside trail."""
    rng = np.random.default_rng(seed); s = size_m; res = _res(s); log = _Log()
    log("world", S.create_world("River valley", s, res))
    log("relief", S.apply_terrain_noise(seed=seed, amplitude=s * 0.014, feature_size_m=s * 0.3, octaves=6))
    log("range", S.create_mountain_range([[0, s * 0.07], [s * 0.35, s * 0.03], [s * 0.7, s * 0.08], [s, s * 0.04]], width=s * 0.17,
                                         height=s * 0.15, roughness=0.6, seed=seed + 1))
    log("border", S.create_border_mountains(width=s * 0.1, height=s * 0.07, roughness=0.6, seed=seed + 2, sides="ew"))
    for i in range(4):
        hx, hy = s * rng.uniform(0.15, 0.85), s * rng.uniform(0.3, 0.85)
        log(f"hill_{i}", S.create_mountain(hx, hy, s * rng.uniform(0.09, 0.13), s * rng.uniform(0.02, 0.032), profile=0.9, roughness=0.1,
                                           seed=seed + 20 + i))                 # rounded, grassy hills (not rocky lumps)
    x0, x1 = s * rng.uniform(0.35, 0.65), s * rng.uniform(0.35, 0.65)
    course = _meander([x0, s * 0.17], [x1, s - 2], rng, amp=s * 0.09, waves=1.6)
    log("floodplain", S.create_valley(course, width=s * 0.11, depth=s * 0.011))
    log("river", S.create_river(course, width=max(10.0, s * 0.008), depth=2.5, bank_width=max(12.0, s * 0.01)))
    # oxbow lake beside the middle of the river
    mid = np.asarray(course[len(course) // 2]); nxt = np.asarray(course[len(course) // 2 + 1])
    t_ = (nxt - mid) / np.linalg.norm(nxt - mid); nrm = np.array([-t_[1], t_[0]]) * (1 if rng.random() < 0.5 else -1)
    lc = mid + nrm * s * 0.1
    log("oxbow_lake", S.create_lake(_blob(lc[0], lc[1], s * 0.045, rng, n=16, sx=1.4, sy=0.7), depth=3.0))
    def beside(k, d):                                                  # a point d metres to the side of the river at sample k
        a, b = np.asarray(course[k - 1]), np.asarray(course[k + 1]); tt = (b - a) / np.linalg.norm(b - a)
        return (course[k][0] - tt[1] * d, course[k][1] + tt[0] * d)
    start = _dry_point([beside(k, sd * s * f) for k in (-6, -8, -10, -4) for f in (0.04, 0.06, 0.08) for sd in (1, -1)])
    ends = [_dry_point([beside(k, sd * s * f) for f in (0.04, 0.07) for sd in (1, -1)]) for k in (8, 12, 16)]
    _retry(log, "riverside_trail", [lambda e=e: S.create_trail(list(start), list(e), width=6, max_slope=0.25) for e in ends], optional=True)
    log("style", S.set_world_style(snow_line=s * 0.13))
    if trees:
        log("vegetation", S.scatter_vegetation(["oak_trees", "pine_forest", "bushes", "meadow_shrubs", "riparian_reeds", "cliff_rocks"],
                                               seed=seed + 4))
    return _finish("river_valley", s, res, seed, log, start, "the ranges at the north, east and west frame the valley; the river can be waded")


# ------------------------------------------------------------------------------------------------ canyon
def canyon(size_m: float = 1536.0, seed: int = 1, trees: bool = True) -> dict:
    """A high tableland cut by a winding canyon with stepped walls (ledges every few metres, Grand-Canyon-like) and a river at the bottom,
    a side gorge joining it, buttes standing on the rim, and a trail that switchbacks from the rim down to the river."""
    rng = np.random.default_rng(seed); s = size_m; res = _res(s); log = _Log()
    top = s * 0.065
    log("world", S.create_world("Canyon", s, res, base_height=top))
    log("relief", S.apply_terrain_noise(seed=seed, amplitude=s * 0.008, feature_size_m=s * 0.3, octaves=5))
    course = _meander([s * rng.uniform(0.3, 0.7), 0], [s * rng.uniform(0.3, 0.7), s], rng, amp=s * 0.12, waves=1.4)
    log("canyon", S.create_valley(course, width=s * 0.07, depth=top * 1.05))
    j = len(course) // 2 + int(rng.integers(-5, 5))
    side_dir = 1 if course[j][0] < s * 0.5 else -1
    gorge = _meander([s * (0.5 + side_dir * 0.48), s * rng.uniform(0.3, 0.7)], course[j], rng, amp=s * 0.04, waves=1.0, n=20)
    log("side_gorge", S.create_valley(gorge, width=s * 0.035, depth=top * 0.75))
    for i in range(3):
        while True:                                                      # buttes on the rim, away from the canyon
            bx, by = s * rng.uniform(0.1, 0.9), s * rng.uniform(0.1, 0.9)
            if min(math.hypot(bx - p[0], by - p[1]) for p in course + gorge) > s * 0.12:
                break
        log(f"butte_{i}", S.create_plateau(_blob(bx, by, s * rng.uniform(0.03, 0.05), rng), top + s * rng.uniform(0.02, 0.035), wall_width=4))
    log("strata", S.terrace_terrain(step=s * 0.011, riser=0.14, warp=s * 0.005, seed=seed + 3))
    log("border", S.create_border_mountains(width=s * 0.08, height=s * 0.06, roughness=0.6, seed=seed + 4, sides="ew"))
    log("river", S.create_river(course, width=max(8.0, s * 0.006), depth=2.0, bank_width=8.0))
    rim = np.asarray(course[len(course) // 3]) + np.array([s * 0.09 * side_dir * -1, 0])
    floor = course[len(course) // 3]
    _retry(log, "trail_down", [lambda sl=sl: S.create_trail(list(rim), [floor[0] + s * 0.012 * -side_dir, floor[1]], width=6, max_slope=sl)
                               for sl in (0.3, 0.36, 0.42)], optional=True)
    log("style", S.set_world_style(snow_line="off"))
    # dry country: sand and dirt over the tableland (grass stays only where the paint is thin: the canyon floor by the river)
    # (light: painted weights also cover the walls, which should keep their rock colour)
    log("dry_ground", S.paint_terrain_material(s * 0.5, s * 0.5, s * 0.72, "dirt", strength=0.38, hardness=0.85))
    log("dry_sand", S.paint_terrain_material(s * rng.uniform(0.3, 0.7), s * rng.uniform(0.3, 0.7), s * 0.35, "sand", strength=0.22, hardness=0.3))
    if trees:
        log("vegetation", S.scatter_vegetation(species=[
            dict(name="pine", min_sep=16, density=0.45, max_slope=22, radius=2.4, height=11, clusters=0.85, altitude=(top * 0.7, 1e9),
                 cliff_dist=(5, 1e9), scale=(0.7, 1.3)),
            dict(name="shrub", min_sep=8, density=0.35, max_slope=30, radius=1.2, height=1.3, clusters=0.6, scale=(0.7, 1.3)),
            dict(name="rock", min_sep=9, density=0.5, max_slope=80, radius=2.2, height=1.6, cliff_dist=(0, 20), scale=(0.5, 1.8)),
            dict(name="reed", min_sep=4, density=0.8, max_slope=15, radius=0.6, height=1.4, water_dist=(1, 8)),
            dict(name="oak", min_sep=20, density=0.5, max_slope=15, radius=4.6, height=9.5, altitude=(-1e9, top * 0.3), water_dist=(4, 30),
                 scale=(0.7, 1.2))], seed=seed + 5))
    return _finish("canyon", s, res, seed, log, tuple(rim), "the canyon floor is reached by the switchback trail; walls are climbable")
