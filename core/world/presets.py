"""High-level landscape generators: one call builds a complete, validated world."""
from __future__ import annotations

import numpy as np

from core.world import service as S

PRESETS = {
    "highland_lake": "Bowl valley with terraced cliff tiers, turquoise lake with an island, groves and meadows, rocks, "
                     "ringed by snowy mountains, a river that falls down the tiers as waterfalls, and a climbable mountain (spiral ramp to a flat summit).",
}


def _poly(cx: float, cy: float, r: float, rng: np.random.Generator, n: int = 7) -> list[list[float]]:
    return [[float(cx + np.cos(a) * r * rng.uniform(0.8, 1.2)), float(cy + np.sin(a) * r * rng.uniform(0.75, 1.15))]
            for a in np.linspace(0, 2 * np.pi, n, endpoint=False)]


def highland_lake(size_m: float = 1536.0, seed: int = 1, trees: bool = True) -> dict:
    """Genshin-style highland basin. size_m: world side in meters (>= 768 recommended)."""
    rng = np.random.default_rng(seed)
    res = int(size_m / 2) // 4 * 4 + 1                                     # ~2 m cells, (res-1) divisible by 4
    log: list[dict] = []

    def step(name, r):
        log.append({"step": name, "ok": r.get("ok", False), "warnings": [w.get("code") for w in r.get("warnings", [])],
                    **({"error": r.get("error")} if not r.get("ok", False) else {})})
        return r

    c = size_m * 0.5; cz = size_m * (0.50 + 0.04 * rng.uniform(-1, 1))
    r = S.create_world("Highland lake", size_m, res); log.append({"step": "world", "ok": r["ok"]})
    step("relief", S.apply_terrain_noise(seed=seed, amplitude=size_m * 0.009, feature_size_m=size_m * 0.33, octaves=5))
    step("bowl", S.create_bowl(c, cz, size_m * 0.20, size_m * 0.36, size_m * 0.10, warp=size_m * 0.07, seed=seed + 1))
    step("terraces", S.terrace_terrain(step=size_m * 0.022, riser=0.08, warp=size_m * 0.007, seed=seed + 2,
                                       center=[c, cz], r_inner=size_m * 0.215, r_outer=size_m * 0.30))
    step("mountains", S.create_border_mountains(width=size_m * 0.22, height=size_m * 0.15, roughness=0.65, seed=seed + 3))
    step("style", S.set_world_style(snow_line=size_m * 0.19))
    lake_c = (c + size_m * 0.015 * rng.uniform(-1, 1), cz + size_m * 0.02)
    lake_poly = _poly(*lake_c, size_m * 0.085, rng)
    step("lake", S.create_lake(lake_poly, depth=size_m * 0.0045))
    step("island", S.create_mountain(lake_c[0] + size_m * 0.012, lake_c[1], size_m * 0.027, size_m * 0.011, profile=1.1, roughness=0.1, seed=seed))
    # a river that descends the tiers from the north-west rim into the lake: every cliff it crosses becomes a waterfall
    # (on some seeds the first spot on the rim is lower than the lake: try further along the rim; a failed attempt applies nothing)
    river, tries = {}, 0
    for ang in (-114, -100, -128, -86, -142, -72):
        tries += 1
        a = np.radians(ang)
        src = [c + np.cos(a) * size_m * 0.32, cz + np.sin(a) * size_m * 0.32] if tries > 1 else [c - size_m * 0.13, cz - size_m * 0.29]
        # aim at the lake's near shore (a bit inside), not its centre: the island sits there and a river can't climb onto it
        lp = np.asarray(lake_poly, np.float64); near = lp[np.argmin(np.hypot(lp[:, 0] - src[0], lp[:, 1] - src[1]))]
        mouth = list(near + (np.asarray(lake_c) - near) * 0.3)
        river = S.create_river_auto(src, mouth, width=max(5.0, size_m * 0.0045), depth=2.0, bank_width=6.0)
        if river.get("ok"):
            break
    log.append({"step": "river", "ok": river.get("ok", False), "warnings": [w.get("code") for w in river.get("warnings", [])],
                "waterfalls": len(river.get("falls", [])), "attempts": tries, **({"error": river.get("error")} if not river.get("ok") else {})})
    # a climbable hero mountain on the open meadow: constant-grade spiral ramp to a flat summit (no cliffs in its way)
    ang = rng.uniform(-0.6, 0.6)
    mx, mz = c + np.cos(ang) * size_m * 0.17, cz + np.sin(ang) * size_m * 0.17 - size_m * 0.02
    cm = S.create_climbable_mountain(mx, mz, size_m * 0.055, size_m * 0.058, grade=0.22, width=max(6.0, size_m * 0.0042), seed=seed + 5)
    log.append({"step": "climbable_mountain", "ok": cm.get("ok", False), "warnings": [w.get("code") for w in cm.get("warnings", [])]})
    # trail from the south meadow to the foot of the mountain, and another that climbs the tiers to the north rim (cuts ramps through the cliffs)
    start = [c - size_m * 0.03, cz + size_m * 0.17]
    ramp_start = cm.get("ramp_start", [mx, mz])
    step("trail_to_mountain", S.create_trail(start, ramp_start, width=max(6.0, size_m * 0.0042), max_slope=0.28))
    north = [c - size_m * 0.06, size_m * 0.20]
    tiers, tries = {}, 0
    for slope, dx in ((0.30, -0.06), (0.30, 0.06), (0.34, -0.12), (0.34, 0.12), (0.38, 0.0)):
        tries += 1
        tiers = S.create_trail(start, [c + dx * size_m, size_m * 0.20], width=max(6.0, size_m * 0.0042), max_slope=slope)
        if tiers.get("ok"):
            break
    log.append({"step": "trail_tiers", "ok": tiers.get("ok", False), "warnings": [w.get("code") for w in tiers.get("warnings", [])],
                "attempts": tries, **({"error": tiers.get("error")} if not tiers.get("ok") else {})})
    peak = [mx, mz]
    if trees:
        step("vegetation", S.scatter_vegetation(["oak_trees", "pine_forest", "bushes", "cliff_rocks"], seed=seed + 4))
    final = S.analyze_traversability(start=start, max_slope_deg=35)
    from features.paths.routing import route
    walk = route(S.SESSION.hf, S.SESSION.op_log, tuple(start), tuple(peak), max_grade=float(np.tan(np.radians(35))))   # is the climbable summit reachable on foot?
    summit = {"x": peak[0], "y": peak[1], "height": float(S.SESSION.hf.sample(*peak)), "reachable": bool(walk["ok"]),
              "route_length_m": walk.get("length")}
    return {"ok": all(s["ok"] for s in log), "preset": "highland_lake", "size_m": size_m, "resolution": res, "seed": seed, "steps": log,
            "traversability": {"reachable_fraction": final["reachable_fraction"], "summit_reachable": summit["reachable"], "summit": summit,
                               "unreachable_regions": len(final["unreachable_regions"]),
                               "note": "the ring of border mountains is deliberately not walkable; the climbable mountain is"}}


GENERATORS = {"highland_lake": highland_lake}


def generate_landscape(preset: str = "highland_lake", size_m: float = 1536.0, seed: int = 1) -> dict:
    """Generate a complete landscape in one call (replaces the current world). Presets: highland_lake. Then inspect with capture_world_view and validate_world."""
    if preset not in GENERATORS:
        return {"ok": False, "error": f"unknown preset {preset!r}; available: {list(PRESETS)}"}
    if size_m < 512 or size_m > 6000:
        return {"ok": False, "error": "size_m must be between 512 and 6000"}
    try:
        return GENERATORS[preset](size_m, seed)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
