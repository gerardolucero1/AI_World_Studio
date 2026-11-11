"""Semantic landscape tools shared by the REST API and the MCP server.

Conventions: units are METERS; world origin (0,0) is the north-west corner, x grows east, y grows south;
heights are meters above the zero datum. Every tool returns JSON-serializable data and never raises for
expected problems (returns {"ok": False, "error": ...})."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from biomes.masks.masks import slope_degrees, water_mask
from core.history.operations import Operation
from core.tasks.tasks import TaskManager
from core.world.session import WorldSession
from materials.materials import MATERIALS, final_weights
from render import preview
from validation.validator import validate_world as _validate

SESSION = WorldSession()
TASKS = TaskManager()
LARGE_OPS = {"noise", "mountain_range", "hydraulic_erosion", "thermal_erosion", "cliff_plateau"}
Pt = list[float]

VEGETATION_PRESETS: dict[str, list[dict] | dict] = {
    "oak_trees": dict(name="oak", min_sep=11, density=0.5, max_slope=22, radius=4.6, height=9.5, clusters=0.85, water_dist=(4, 1e9), scale=(0.75, 1.5)),
    "pine_forest": dict(name="pine", min_sep=8, density=0.55, max_slope=36, radius=2.6, height=13, clusters=0.9, water_dist=(4, 1e9), scale=(0.8, 1.4)),
    "conifer_forest": dict(name="pine", min_sep=9, density=0.8, max_slope=28, radius=2.5, height=11, clusters=0.6, altitude=(-1e9, 1e9)),
    "bushes": dict(name="shrub", min_sep=5, density=0.45, max_slope=32, radius=1.3, height=1.4, clusters=0.5, water_dist=(3, 1e9)),
    "meadow_shrubs": dict(name="shrub", min_sep=5, density=0.5, max_slope=35, radius=1.3, height=1.6, water_dist=(3, 1e9)),
    "riparian_reeds": dict(name="reed", min_sep=4, density=0.9, max_slope=20, radius=0.6, height=1.4, water_dist=(1, 12)),
    "cliff_rocks": [dict(name="rock", min_sep=9, density=0.55, max_slope=80, radius=2.4, height=1.8, cliff_dist=(0, 28), scale=(0.5, 1.8)),
                    dict(name="rock", min_sep=30, density=0.25, max_slope=25, radius=1.6, height=1.2, scale=(0.5, 1.4))],
    "alpine_rocks": dict(name="rock", min_sep=14, density=0.4, max_slope=60, radius=1.5, height=1.2, altitude=(60, 1e9)),
}


def _err(e: Exception) -> dict:
    return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _brief(res: dict | None) -> dict:
    if not res:
        return {}
    return {k: v for k, v in res.items() if not k.startswith("_") and k != "warnings"
            and not (isinstance(v, list) and len(v) > 12)}


def _rect_m(rect) -> dict | None:
    if not rect:
        return None
    c = SESSION.hf.cell_size
    return {"x0": rect[2] * c, "x1": rect[3] * c, "y0": rect[0] * c, "y1": rect[1] * c}


def _apply(kind: str, params: dict) -> dict:
    s = SESSION
    try:
        with s.lock:
            if kind in LARGE_OPS:                       # checkpoint before large operations
                s.checkpoint(f"auto-{s.revision}")
                for k in [k for k in s.checkpoints if k.startswith("auto-")][:-5]:
                    del s.checkpoints[k]
            op = Operation(kind, params)
            rect = s.apply(op)
            warns = (op.result or {}).get("warnings", [])
            return {"ok": True, "operation": kind, "revision": s.revision, "affected_region_m": _rect_m(rect),
                    "result": _brief(op.result), "warnings": warns, "operation_index": len(s.op_log) - 1}
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ------------------------------------------------------------------ project & terrain
def create_world(name: str = "Untitled", size_m: float = 1024.0, resolution: int = 513, base_height: float = 0.0) -> dict:
    """Create a new empty square world. size_m: side length in meters. resolution: vertices per side (257..2049). Replaces the current world."""
    try:
        if not 33 <= resolution <= 4097:
            raise ValueError("resolution must be in 33..4097")
        SESSION.new(size_m, resolution, name, base_height)
        return get_world_info()
    except Exception as e:  # noqa: BLE001
        return _err(e)


def get_world_info() -> dict:
    """World name, size, resolution, revision, operation count, checkpoints."""
    s = SESSION
    sx, sz = s.hf.size
    return {"ok": True, "name": s.name, "size_m": [sx, sz], "resolution": [s.hf.nx, s.hf.ny], "cell_size_m": s.hf.cell_size,
            "revision": s.revision, "operations": len(s.op_log), "checkpoints": list(s.checkpoints), "path": str(s.path) if s.path else None}


def get_terrain_info() -> dict:
    """Elevation and slope statistics for the whole terrain."""
    return inspect_terrain()


def save_world(path: str) -> dict:
    """Save the project as an .awp folder (heightfield + operation log)."""
    try:
        return {"ok": True, "path": str(SESSION.save(path))}
    except Exception as e:  # noqa: BLE001
        return _err(e)


def load_world(path: str) -> dict:
    """Load an .awp project folder."""
    try:
        SESSION.load(path)
        return get_world_info()
    except Exception as e:  # noqa: BLE001
        return _err(e)


def sculpt_terrain(tool: str, points: list[Pt], radius: float = 20.0, strength: float = 0.5, hardness: float = 0.0,
                   target_height: float = 0.0, repeats: int = 1) -> dict:
    """Brush stroke along `points` ([[x,y],...] meters). tool: raise|lower|smooth|flatten. strength 0..1. For flatten set target_height (m)."""
    return _apply("sculpt", dict(tool=tool, points=points, radius=radius, strength=strength, hardness=hardness,
                                 target_height=target_height, repeats=repeats))


def raise_terrain(x: float, y: float, radius: float = 30.0, strength: float = 0.5, repeats: int = 10) -> dict:
    """Raise terrain around (x,y) meters."""
    return sculpt_terrain("raise", [[x, y]], radius, strength, repeats=repeats)


def lower_terrain(x: float, y: float, radius: float = 30.0, strength: float = 0.5, repeats: int = 10) -> dict:
    """Lower terrain around (x,y) meters."""
    return sculpt_terrain("lower", [[x, y]], radius, strength, repeats=repeats)


def smooth_terrain(x: float, y: float, radius: float = 30.0, strength: float = 0.8, repeats: int = 5) -> dict:
    """Smooth terrain around (x,y) meters."""
    return sculpt_terrain("smooth", [[x, y]], radius, strength, repeats=repeats)


def flatten_terrain(x: float, y: float, height: float, radius: float = 30.0, strength: float = 1.0, repeats: int = 5) -> dict:
    """Flatten terrain around (x,y) to an absolute height in meters."""
    return sculpt_terrain("flatten", [[x, y]], radius, strength, target_height=height, repeats=repeats)


def apply_terrain_noise(seed: int = 0, amplitude: float = 30.0, feature_size_m: float = 300.0, octaves: int = 6, ridged: bool = False) -> dict:
    """Add fractal noise. amplitude: peak height in m; feature_size_m: size of the largest features."""
    return _apply("noise", dict(seed=seed, amplitude=amplitude, base_freq=1.0 / feature_size_m, octaves=octaves, ridged=ridged))


def create_mountain(x: float, y: float, radius: float, height: float, profile: float = 1.5, roughness: float = 0.15, seed: int = 0) -> dict:
    """Radial mountain centred at (x,y). profile>1 sharper peak. roughness 0..1 ridged detail."""
    return _apply("mountain", dict(x=x, y=y, radius=radius, height=height, profile=profile, roughness=roughness, seed=seed))


def create_mountain_range(path: list[Pt], width: float, height: float, roughness: float = 0.3, seed: int = 0) -> dict:
    """Mountain range along a polyline [[x,y],...]; width = half-width in meters."""
    return _apply("mountain_range", dict(path=path, width=width, height=height, roughness=roughness, seed=seed))


def create_valley(path: list[Pt], width: float, depth: float) -> dict:
    """Carve a valley along a polyline; width = half-width (m), depth (m)."""
    return _apply("valley", dict(path=path, width=width, depth=depth))


def create_bowl(x: float, y: float, r_inner: float, r_outer: float, height: float, warp: float = 0.0, seed: int = 0) -> dict:
    """Basin ringed by highlands: terrain outside r_inner rises to `height` m at r_outer. warp (m) makes the rim organic."""
    return _apply("bowl", dict(x=x, y=y, r_inner=r_inner, r_outer=r_outer, height=height, warp=warp, seed=seed))


def terrace_terrain(step: float = 24.0, riser: float = 0.1, warp: float = 8.0, seed: int = 0, strength: float = 1.0,
                    center: Pt | None = None, r_inner: float = 0.0, r_outer: float = 0.0, min_height: float | None = None) -> dict:
    """Turn slopes into flat grassy benches separated by steep cliff faces (stepped highlands, Genshin-style mesas).
    step: bench height difference (m); riser: fraction of each step that is the steep face (smaller = sheerer);
    warp: contour irregularity (m). center/r_inner/r_outer: leave the area inside r_inner untouched (blend until r_outer)."""
    mask = [center[0], center[1], r_inner, r_outer] if center else None
    return _apply("terrace", dict(step=step, riser=riser, warp=warp, seed=seed, strength=strength, radial_mask=mask, min_height=min_height))


def create_border_mountains(width: float = 300.0, height: float = 220.0, roughness: float = 0.6, seed: int = 0, sides: str = "nesw") -> dict:
    """Mountain ring along the world edges (hides the map border). width = how far inward (m); sides: letters n,e,s,w."""
    return _apply("border_mountains", dict(width=width, height=height, roughness=roughness, seed=seed, sides=sides))


def create_basin(x: float, y: float, radius: float, depth: float, flat: float = 0.4) -> dict:
    """Circular depression (lake bed / crater) with flat floor fraction `flat`."""
    return _apply("basin", dict(x=x, y=y, radius=radius, depth=depth, flat=flat))


def create_plateau(polygon: list[Pt], height: float, wall_width: float = 20.0) -> dict:
    """Plateau at absolute `height` (m) inside polygon with a sloped heightfield wall. For real rock walls use create_cliff."""
    return _apply("plateau", dict(polygon=polygon, height=height, wall_width=wall_width))


def create_cliff(polygon: list[Pt], height: float, strata: int = 6, ledge: float = 1.0, overhang: float = 1.5,
                 flare: float = 3.0, roughness: float = 2.0, seed: int = 0) -> dict:
    """Plateau at absolute `height` (m) with independent rock-wall geometry (strata ledges, overhang, base flare)."""
    return _apply("cliff_plateau", dict(polygon=polygon, height=height, strata=strata, ledge=ledge, overhang=overhang,
                                        flare=flare, roughness=roughness, seed=seed))


def apply_erosion(kind: str = "hydraulic", iterations: int = 40, background: bool = False) -> dict:
    """Erode the whole terrain. kind: hydraulic|thermal. background=True runs as a task (see get_task_status)."""
    k = {"hydraulic": "hydraulic_erosion", "thermal": "thermal_erosion"}.get(kind)
    if k is None:
        return {"ok": False, "error": "kind must be 'hydraulic' or 'thermal'"}
    if not background:
        return _apply(k, dict(iterations=iterations))
    tid = TASKS.submit(f"{k} x{iterations}", lambda: _apply(k, dict(iterations=iterations)))
    return {"ok": True, "task_id": tid, "status": TASKS.status(tid)["status"]}


def get_task_status(task_id: str) -> dict:
    """Status of a background task: queued|running|completed|failed|cancelled."""
    try:
        return {"ok": True, **TASKS.status(task_id)}
    except KeyError as e:
        return _err(e)


def cancel_task(task_id: str) -> dict:
    """Cancel a task that is still queued."""
    try:
        return {"ok": TASKS.cancel(task_id)}
    except KeyError as e:
        return _err(e)


def set_world_style(snow_line: float | str | None = None, rock_slope_min: float | None = None, rock_slope_max: float | None = None) -> dict:
    """World look settings. snow_line: height in m where snow starts, 'off' = no snow, None = unchanged ('auto' resets to 85% of the highest point).
    rock_slope_min/max: slope (degrees) where surfaces start/finish turning into rock (default 28/42)."""
    with SESSION.lock:
        st = SESSION.hf.style
        if snow_line == "auto":
            st.pop("snow_line", None)
        elif snow_line is not None:
            st["snow_line"] = snow_line
        if rock_slope_min is not None or rock_slope_max is not None:
            cur = st.get("rock_slope", (28.0, 42.0))
            st["rock_slope"] = (rock_slope_min if rock_slope_min is not None else cur[0], rock_slope_max if rock_slope_max is not None else cur[1])
        SESSION.revision += 1
        return {"ok": True, "style": dict(st)}


# ------------------------------------------------------------------ features
def create_river(path: list[Pt], width: float = 10.0, depth: float = 3.0, bank_width: float = 12.0) -> dict:
    """River from path[0] (upstream) to path[-1]: carves a downhill channel. Warns if terrain rises against the flow."""
    return _apply("river", dict(path=path, width=width, depth=depth, bank_width=bank_width))


def create_river_auto(start: Pt, end: Pt, width: float = 6.0, depth: float = 2.0, bank_width: float = 6.0) -> dict:
    """River that finds its own course from start (upstream, high) to end (e.g. a lake) flowing only downhill, crossing cliffs where the drop is smallest.
    Every cliff it crosses becomes a WATERFALL (with a plunge pool). Returns the number of falls and their heights."""
    from features.paths.routing import route_river
    try:
        with SESSION.lock:
            rt = route_river(SESSION.hf, tuple(start), tuple(end))
        if not rt["ok"]:
            return rt
        res = _apply("river", dict(path=rt["points"], width=width, depth=depth, bank_width=bank_width))
        if res["ok"]:
            rep = SESSION.op_log[-1].result or {}
            res["falls"] = [{"height_m": round(f["height"], 1), "top": f["top"]} for f in rep.get("falls", [])]
            res["course"] = {"length_m": round(rt["length"]), "drop_m": round(rt["drop"], 1)}
        return res
    except Exception as e:  # noqa: BLE001
        return _err(e)


def create_lake(polygon: list[Pt], depth: float = 4.0, level: float | None = None, shore_width: float = 15.0) -> dict:
    """Lake inside polygon. level=None => lowest rim height (no leaks)."""
    return _apply("lake", dict(polygon=polygon, depth=depth, level=level, shore_width=shore_width))


def create_path(path: list[Pt], width: float = 4.0, max_slope: float = 0.25, bank_width: float = 6.0) -> dict:
    """Trail along polyline with limited grade (max_slope = rise/run). Warns when switchbacks/ramps are needed."""
    return _apply("path", dict(path=path, width=width, max_slope=max_slope, bank_width=bank_width))


def create_climbable_mountain(x: float, y: float, radius: float, height: float, grade: float = 0.22, width: float = 6.0,
                              clockwise: bool = True, seed: int = 0) -> dict:
    """A mountain designed to be CLIMBED: raises a mountain at (x,y) and carves a spiral ramp of constant grade (rise/run, 0.22 = 12 deg)
    from its base to the summit, with turns far enough apart that they never overlap. The summit is recorded as the world's summit marker.
    Ramp width >= 6 m is recommended: narrower ramps can break the baked navigation mesh (verified with Godot's navigation server).
    Returns the ramp stats (turns, length) and warnings."""
    import math
    try:
        m = _apply("mountain", dict(x=x, y=y, radius=radius, height=height, profile=1.25, roughness=0.06, seed=seed))
        if not m["ok"]:
            return m
        with SESSION.lock:
            hf = SESSION.hf
            r_start, r_end = radius * 0.86, max(radius * 0.04, width * 1.4)     # the ramp must not end in a whirl tighter than its own width
            top = hf.sample(x, y)
            base = hf.sample(x + r_start, y)
            L = max((top - base) / max(grade, 0.05), 1.0) * 1.15                 # ramp length to climb from base to summit (15% margin)
            r_mean = (r_start + r_end) / 2
            turns = max(L / (2 * math.pi * r_mean), 0.8)
            spacing = (r_start - r_end) / turns
            n = max(int(L / 5), 24)
            pts = []
            for i in range(n + 1):
                t = i / n
                ang = (2 * math.pi * turns * t) * (1 if clockwise else -1) + 0.3
                r = r_start + (r_end - r_start) * t
                pts.append([float(x + math.cos(ang) * r), float(y + math.sin(ang) * r)])
        warn = []
        if spacing < width * 3.2:
            warn.append({"severity": "warning", "code": "turns_too_close", "message": f"Ramp turns are only {spacing:.0f} m apart (width {width} m): they may merge.",
                         "suggestion": "Use a larger radius, a lower height, a gentler grade (more turns need more room), or a narrower ramp."})
        p = _apply("path", dict(path=pts, width=width, max_slope=grade, bank_width=width * 1.1, smoothing=max(width * 3, 12.0), max_cut_fill=30.0))
        if not p["ok"]:
            return p
        with SESSION.lock:
            end_h = float(np.array((SESSION.op_log[-1].result or {}).get("elevation", [SESSION.hf.sample(x, y)]))[-1])
        _apply("cap", dict(x=float(x), y=float(y), radius=radius * 0.45, height=end_h, feather=max(width * 1.5, 8.0)))   # flat summit at the ramp's end height
        with SESSION.lock:
            SESSION.hf.style["summit"] = [float(x), float(y)]
            SESSION.revision += 1
        return {"ok": True, "operations": ["mountain", "path", "cap"], "ramp_start": pts[0], "ramp_end": pts[-1], "summit": {"x": x, "y": y, "height": SESSION.hf.sample(x, y)},
                "ramp": {"turns": round(turns, 2), "turn_spacing_m": round(spacing, 1), "length_m": round(L), "grade": grade, "width": width},
                "warnings": warn + p.get("warnings", [])}
    except Exception as e:  # noqa: BLE001
        return _err(e)


def create_trail(start: Pt, end: Pt, waypoints: list[Pt] | None = None, width: float = 4.0, max_slope: float = 0.3,
                 avoid_steep: bool = True) -> dict:
    """Auto-routed hiking trail from start to end (meters): finds the gentlest route around water and cliffs, then carves it with grade <= max_slope
    (rise/run; 0.3 = 17 deg). Across cliff terraces it cuts a ramp. Returns route stats (length, max_grade, climb) and warnings."""
    from features.paths.routing import route
    try:
        with SESSION.lock:
            rt = route(SESSION.hf, SESSION.op_log, tuple(start), tuple(end), [tuple(w) for w in (waypoints or [])],
                       max_grade=None)
        if not rt["ok"]:
            return rt
        res = _apply("path", dict(path=rt["points"], width=width, max_slope=max_slope, bank_width=width * 1.3, max_cut_fill=14.0))
        res["route"] = {k: rt[k] for k in ("length", "max_grade", "mean_grade", "climb")}
        res["waypoints_used"] = len(rt["points"])
        return res
    except Exception as e:  # noqa: BLE001
        return _err(e)


def analyze_traversability(start: Pt | None = None, max_slope_deg: float = 40.0) -> dict:
    """What can be reached on foot from `start` (default: the flattest central spot) without crossing water or slopes steeper than max_slope_deg.
    Returns reachable fraction, whether the highest summit is reachable, and the unreachable regions (add trails to connect them)."""
    from features.paths.routing import traversability
    try:
        with SESSION.lock:
            t = traversability(SESSION.hf, SESSION.op_log, tuple(start) if start else None, max_slope_deg)
        return {k: v for k, v in t.items() if not k.startswith("_")}
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ------------------------------------------------------------------ biomes & materials
def scatter_vegetation(presets: list[str] | None = None, species: list[dict] | None = None, seed: int = 0,
                       region: list[Pt] | None = None, exclusion: list[list[Pt]] | None = None) -> dict:
    """Scatter vegetation by altitude/slope/water rules. presets: oak_trees|pine_forest|bushes|cliff_rocks|riparian_reeds|alpine_rocks (also conifer_forest, meadow_shrubs), or custom species dicts."""
    sp = list(species or [])
    for p in presets or []:
        if p not in VEGETATION_PRESETS:
            return {"ok": False, "error": f"unknown preset {p!r}; choose from {list(VEGETATION_PRESETS)}"}
        v = VEGETATION_PRESETS[p]
        sp.extend(dict(x) for x in (v if isinstance(v, list) else [v]))
    if not sp:
        return {"ok": False, "error": "give presets or species"}
    return _apply("scatter_vegetation", dict(species=sp, seed=seed, region=region, exclusion=exclusion))


def modify_operation(index: int, params: dict) -> dict:
    """Change the parameters of an operation already applied (see list_operations for its index and params), e.g. move a river's
    path, a lake's polygon, a mountain's height, a trail's end. Everything after it is replayed from the original terrain, so dependent
    features adapt. Only the keys you give change. One step: undo restores the previous version."""
    s = SESSION
    try:
        old_kind = s.op_log[index].kind if 0 <= index < len(s.op_log) else None
        rect = s.modify(index, dict(params))
        op = s.op_log[index]
        return {"ok": True, "operation": old_kind, "index": index, "revision": s.revision, "affected_region_m": _rect_m(rect),
                "result": _brief(op.result), "warnings": (op.result or {}).get("warnings", []),
                "later_warnings": {i: [w.get("code") for w in (o.result or {}).get("warnings", [])] for i, o in enumerate(s.op_log)
                                   if i > index and (o.result or {}).get("warnings")}}
    except Exception as e:  # noqa: BLE001
        return _err(e)


def set_operation_enabled(index: int, enabled: bool) -> dict:
    """Turn an applied operation off (or back on) without deleting it: the world is replayed without it, so you can compare
    (e.g. the world without a mountain range, or without the second river). One step: undo restores it."""
    s = SESSION
    try:
        s.modify(index, None, enabled=bool(enabled))
        op = s.op_log[index]
        return {"ok": True, "index": index, "operation": op.kind, "enabled": op.enabled, "revision": s.revision,
                "later_warnings": {i: [w.get("code") for w in (o.result or {}).get("warnings", [])] for i, o in enumerate(s.op_log)
                                   if i > index and (o.result or {}).get("warnings")}}
    except Exception as e:  # noqa: BLE001
        return _err(e)


def place_objects(objects: list[dict]) -> dict:
    """Place individual objects exactly where you want them (a landmark tree, a boulder, a lone pine), grounded on the terrain.
    objects: [{"kind": oak|pine|shrub|reed|rock, "x": m, "y": m, "scale": 1.0 (3 = a huge landmark tree), "yaw": deg, "sink": m into the ground}].
    Exported like vegetation (same meshes). Heights follow later terrain edits."""
    from biomes.scatter.place import place_params
    try:
        return _apply("place_objects", place_params(objects, SESSION.hf.size))
    except Exception as e:  # noqa: BLE001
        return _err(e)


def paint_terrain_material(x: float, y: float, radius: float, material: str, strength: float = 1.0, hardness: float = 0.3) -> dict:
    """Paint a surface material over rule-based materials. material: grass|dirt|rock|snow|sand|mud|wet_rock|gravel."""
    return _apply("paint_material", dict(x=x, y=y, radius=radius, material=material, strength=strength, hardness=hardness))


# ------------------------------------------------------------------ inspection
def _stats(a: np.ndarray) -> dict:
    return {"min": float(a.min()), "max": float(a.max()), "mean": float(a.mean()), "std": float(a.std())}


def inspect_terrain() -> dict:
    """Whole-world stats + 4x4 grid of zones; lists uniform (flat) and vegetation-empty zones."""
    s = SESSION
    with s.lock:
        hf = s.hf; sl = slope_degrees(hf)
        veg = [o.result["_instances"] for o in s.op_log if o.result and "_instances" in o.result]
        veg = np.vstack(veg) if veg else np.zeros((0, 7))
        zones, n = [], 4
        for i in range(n):
            for j in range(n):
                ys, xs = slice(i * hf.ny // n, (i + 1) * hf.ny // n), slice(j * hf.nx // n, (j + 1) * hf.nx // n)
                x0, x1 = j * hf.size[0] / n, (j + 1) * hf.size[0] / n; y0, y1 = i * hf.size[1] / n, (i + 1) * hf.size[1] / n
                cnt = int(((veg[:, 0] >= x0) & (veg[:, 0] < x1) & (veg[:, 2] >= y0) & (veg[:, 2] < y1)).sum()) if len(veg) else 0
                zones.append({"x": [x0, x1], "y": [y0, y1], "height_std": float(hf.data[ys, xs].std()),
                              "mean_slope_deg": float(sl[ys, xs].mean()), "vegetation": cnt})
        return {"ok": True, "elevation": _stats(hf.data), "slope_deg": {"mean": float(sl.mean()), "max": float(sl.max())},
                "uniform_zones": [z for z in zones if z["height_std"] < 0.5],
                "empty_vegetation_zones": [z for z in zones if z["vegetation"] == 0] if len(veg) else [],
                "zones": zones, "water_fraction": float(water_mask(hf, s.op_log).mean())}


def inspect_region(x: float, y: float, width: float, height: float) -> dict:
    """Stats for the rectangle with NW corner (x,y) and size width x height (m): elevation, slope, materials, water, vegetation."""
    s = SESSION
    with s.lock:
        hf, c = s.hf, s.hf.cell_size
        x0, x1 = max(int(x / c), 0), min(int((x + width) / c) + 1, hf.nx); y0, y1 = max(int(y / c), 0), min(int((y + height) / c) + 1, hf.ny)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return {"ok": False, "error": "region outside world or too small"}
        sub = hf.data[y0:y1, x0:x1]; sl = slope_degrees(hf)[y0:y1, x0:x1]
        w = final_weights(hf, s.op_log)[y0:y1, x0:x1].mean((0, 1))
        wm = water_mask(hf, s.op_log)[y0:y1, x0:x1]
        counts = {}
        for o in s.op_log:
            if o.result and "_instances" in o.result:
                i = o.result["_instances"]
                m = (i[:, 0] >= x) & (i[:, 0] < x + width) & (i[:, 2] >= y) & (i[:, 2] < y + height)
                counts[o.kind] = counts.get(o.kind, 0) + int(m.sum())
        return {"ok": True, "elevation": _stats(sub), "slope_deg": {"mean": float(sl.mean()), "max": float(sl.max())},
                "materials": {m: round(float(v), 3) for m, v in zip(MATERIALS, w) if v > 0.005},
                "water_fraction": float(wm.mean()), "vegetation_instances": sum(counts.values())}


def analyze_elevation(bins: int = 8) -> dict:
    """Histogram of elevations."""
    d = SESSION.hf.data; h, e = np.histogram(d, bins=bins)
    return {"ok": True, "bins_m": [float(v) for v in e], "fraction": [float(v) for v in h / d.size], **_stats(d)}


def analyze_slope() -> dict:
    """Fraction of terrain per slope band (degrees)."""
    sl = slope_degrees(SESSION.hf); edges = [0, 5, 15, 30, 45, 60, 90]
    h, _ = np.histogram(sl, bins=edges)
    return {"ok": True, "bands_deg": edges, "fraction": [float(v) for v in h / sl.size], "max_deg": float(sl.max())}


def detect_terrain_issues() -> dict:
    """Terrain-level findings only (extreme slopes, discontinuities, resolution)."""
    from validation.validator import validate_terrain
    return {"ok": True, "findings": validate_terrain(SESSION)}


def validate_world() -> dict:
    """Full validation: terrain + feature warnings + path/water relations. Findings have severity, location, explanation, suggestion."""
    return _validate(SESSION)


def _preview_dir() -> Path:
    base = SESSION.path / "previews" if SESSION.path else Path(tempfile.gettempdir()) / "awstudio" / "previews"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_world_preview(size: int = 512, vegetation: bool = True) -> dict:
    """Top-down shaded map with materials, water and vegetation. Returns the PNG file path."""
    with SESSION.lock:
        im = preview.top_down(SESSION.hf, SESSION.op_log, size, vegetation)
        p = _preview_dir() / f"top_r{SESSION.revision}.png"; im.save(p)
    return {"ok": True, "path": str(p), "revision": SESSION.revision}


def capture_world_view(view: str = "aerial", x: float | None = None, y: float | None = None, yaw: float | None = None,
                       pitch: float | None = None, height_above_ground: float | None = None, fov: float = 60.0,
                       width: int = 480, height: int = 300) -> dict:
    """Render a perspective view. view: aerial (from the south looking north-ish) | player (eye 1.7 m at x,y; yaw deg, 0=east,90=south) | custom.
    pitch in degrees (negative looks down). Returns the PNG path."""
    with SESSION.lock:
        hf = SESSION.hf; sx, sz = hf.size
        if view == "aerial":
            cx, cy = x if x is not None else sx / 2, y if y is not None else sz / 2
            dist = max(sx, sz) * 0.75
            pos = (cx, float(hf.data.max()) + dist * 0.55, cy - dist); yaw_, pit = 90.0, pitch if pitch is not None else -32.0
            if yaw is not None:
                yaw_ = yaw; a = np.radians(yaw_ + 180); pos = (cx + np.cos(a) * dist, pos[1], cy + np.sin(a) * dist)
        else:
            px, py = x if x is not None else sx / 2, y if y is not None else sz / 2
            hag = height_above_ground if height_above_ground is not None else (1.7 if view == "player" else 20.0)
            pos = (px, hf.sample(px, py) + hag, py); yaw_, pit = yaw if yaw is not None else 90.0, pitch if pitch is not None else (-2.0 if view == "player" else -20.0)
        im = preview.perspective(hf, SESSION.op_log, pos, yaw_, pit, fov, width, height)
        p = _preview_dir() / f"{view}_r{SESSION.revision}.png"; im.save(p)
    return {"ok": True, "path": str(p), "camera": {"position": [float(v) for v in pos], "yaw": yaw_, "pitch": pit}, "revision": SESSION.revision}


# ------------------------------------------------------------------ history & versions
def create_checkpoint(name: str) -> dict:
    """Snapshot the current world so it can be restored or compared."""
    SESSION.checkpoint(name); return {"ok": True, "checkpoints": list(SESSION.checkpoints)}


def rollback_to_checkpoint(name: str) -> dict:
    """Restore the world to a checkpoint (undo history is cleared)."""
    if name not in SESSION.checkpoints:
        return {"ok": False, "error": f"unknown checkpoint {name!r}; have {list(SESSION.checkpoints)}"}
    SESSION.rollback(name); return get_world_info()


def compare_world_versions(checkpoint: str) -> dict:
    """Compare the current world with a checkpoint: changed area, max rise/drop, new operations."""
    if checkpoint not in SESSION.checkpoints:
        return {"ok": False, "error": f"unknown checkpoint {checkpoint!r}"}
    with SESSION.lock:
        old, ops = SESSION.checkpoints[checkpoint][:2]; hf = SESSION.hf; d = hf.data - old
        ch = np.abs(d) > 0.01
        box = None
        if ch.any():
            ys, xs = np.where(ch); c = hf.cell_size
            box = {"x": [float(xs.min() * c), float(xs.max() * c)], "y": [float(ys.min() * c), float(ys.max() * c)]}
        return {"ok": True, "changed_fraction": float(ch.mean()), "max_rise_m": float(d.max()), "max_drop_m": float(d.min()),
                "bbox_m": box, "mean_elevation_change": float(d.mean()),
                "new_operations": [o.kind for o in SESSION.op_log if o not in ops], "removed_operations": [o.kind for o in ops if o not in SESSION.op_log]}


def undo() -> dict:
    """Undo the last step."""
    SESSION.undo(); return get_world_info()


def redo() -> dict:
    """Redo the last undone step."""
    SESSION.redo(); return get_world_info()


def list_operations() -> dict:
    """Operations applied so far (kind, params, warnings)."""
    return {"ok": True, "operations": [{"index": i, "kind": o.kind, "params": o.params, "enabled": o.enabled,
                                        "warnings": [w.get("code") for w in (o.result or {}).get("warnings", [])]}
                                       for i, o in enumerate(SESSION.op_log)]}


def generate_landscape(preset: str = "highland_lake", size_m: float = 1536.0, seed: int = 1) -> dict:
    """Generate a complete landscape in one call (replaces the current world). Presets: highland_lake (terraced basin, lake with island, groves, rocks, snowy ring, climbing trails)."""
    from core.world import presets
    return presets.generate_landscape(preset, size_m, seed)


def get_capabilities() -> dict:
    """What this studio can do: conventions, landscape presets, vegetation presets, materials, and the recommended workflow. Call this first."""
    from core.world import presets
    from materials.materials import MATERIALS
    return {"ok": True, "units": "meters; origin = north-west corner; x east, y south; heights above datum",
            "landscape_presets": presets.PRESETS, "vegetation_presets": list(VEGETATION_PRESETS), "materials": MATERIALS,
            "godot_target": "4.7.x", "tool_count": len(TOOLS),
            "recommended_flow": ["generate_landscape (or create_world + shapes)", "terrace_terrain / create_border_mountains for cliffs and framing",
                                 "create_lake / create_river", "create_trail (auto-routed, carves ramps)", "scatter_vegetation with presets",
                                 "analyze_traversability + validate_world", "capture_world_view (aerial + player) and look at them",
                                 "fix issues; create_checkpoint before big changes", "save_world, export_to_godot(godot_project=...)"],
            "notes": ["Large operations auto-create checkpoints (rollback_to_checkpoint).", "Every tool returns {ok: false, error} instead of raising.",
                      "Warnings carry a suggestion: act on them or tell the user."]}


# ------------------------------------------------------------------ Godot export
EXPORT_STATE: dict[str, Any] = {"last": None}


def prepare_godot_export(chunk_cells: int | None = None) -> dict:
    """Dry run: chunk grid, LOD levels, instance counts and likely problems, without writing files."""
    from exporters.godot.terrain_lod import lod_steps_for, pick_chunk_cells
    try:
        with SESSION.lock:
            hf = SESSION.hf
            cells = chunk_cells or pick_chunk_cells(hf)
            inst = sum(len(o.result["_instances"]) for o in SESSION.op_log if o.result and "_instances" in o.result)
            feats = {k: sum(o.kind == k for o in SESSION.op_log) for k in ("lake", "river", "cliff_plateau")}
            issues = []
            if inst > 300_000:
                issues.append({"severity": "warning", "code": "too_many_instances", "message": f"{inst} vegetation instances"})
            v = _validate(SESSION)
            if not v["ok"]:
                issues.append({"severity": "warning", "code": "world_has_errors", "message": "validate_world reports errors; export anyway or fix first"})
            return {"ok": True, "chunk_cells": cells, "chunk_grid": [(hf.nx - 1) // cells, (hf.ny - 1) // cells],
                    "lod_steps": lod_steps_for(cells), "vegetation_instances": inst, "features": feats, "target": "Godot 4.7.2",
                    "issues": issues}
    except Exception as e:  # noqa: BLE001
        return _err(e)


def export_to_godot(path: str | None = None, chunk_cells: int | None = None, res_prefix: str = "res://", verify: bool = False,
                    background: bool = False, godot_project: str | None = None, subfolder: str = "GodotWorld",
                    profile: str = "full", include_preview: bool = True, navigation: str = "auto", grass: bool = True,
                    grass_spacing: float = 1.5, terrain: str = "mesh", terrain3d_addon: str | None = None,
                    terrain3d_material: str | None = None, terrain3d_assets: str | None = None,
                    terrain3d_texture_ids: dict | None = None, terrain3d_collision: str = "full", water_script: str | None = None) -> dict:
    """Export the world as a Godot 4.7 scene package (World.tscn + GLB chunks with LOD/collision, water, cliffs, vegetation MultiMeshes,
    stylized materials/shaders, textures, manifest). Two ways to call it:
      - godot_project='/path/to/my/godot/project' -> writes <project>/<subfolder>/ with res://<subfolder>/ paths (project.godot untouched);
      - path='/some/empty/folder' -> a standalone Godot project you can open directly.
    World.tscn contains ONLY the world (terrain, water, cliffs, vegetation, PlayerStart marker, Trail_*/River_* Path3D) so it drops into any scene;
    Preview.tscn adds sun/sky/camera for a quick look (include_preview). profile='compatible' also runs on the Compatibility renderer.
    grass: tall grass tufts placed on the GPU (no per-tuft data stored); grass_spacing = metres between tufts (denser looks lusher but draws more).
    navigation: 'auto' (default: bake a NavigationMesh with Godot's own baker if the Godot binary is found, else skip) | 'bake' | 'python' (coarse; narrow trails may disconnect) | 'none'.
    terrain: 'mesh' (GLB chunks, no addon needed) | 'terrain3d' (a Terrain3D node with baked regions + 8 textures; needs the Terrain3D addon in
    godot_project/addons/terrain_3d, or terrain3d_addon=<addon folder>; standalone exports bundle the addon). To use the game's own Terrain3D look pass
    terrain3d_material / terrain3d_assets (res:// paths in the project) and terrain3d_texture_ids ({"grass": 0, "rock": 1, ...}: our 8 materials ->
    its texture slots). terrain3d_collision: 'full' | 'dynamic'.
    Water is also written as data (WorldData/water.json + surface.r32: river ribbons with simulated waterfalls, lake cells, a surface grid
    for wading/swimming); water_script='res://…/water.gd' puts the game's own water node in World.tscn instead of our water meshes.
    Refuses to overwrite a non-empty folder that is not a previous export. verify=True imports it in the real Godot (headless) and checks it."""
    from pathlib import Path as _P
    from exporters.godot.exporter import ExportSettings, export_world, verify_with_godot
    try:
        write_project = True
        if godot_project:
            gp = _P(godot_project)
            if not (gp / "project.godot").exists():
                return {"ok": False, "error": f"{gp} has no project.godot: pass the folder of an existing Godot project"}
            path = str(gp / subfolder); res_prefix = f"res://{subfolder}/"; write_project = False
        if not path:
            return {"ok": False, "error": "give path (standalone export) or godot_project (existing project)"}
        out_path = path
    except Exception as e:  # noqa: BLE001
        return _err(e)

    def job():
        r = export_world(SESSION, out_path, ExportSettings(chunk_cells=chunk_cells, res_prefix=res_prefix, write_project_file=write_project, profile=profile, include_preview=include_preview, navigation=navigation, grass=grass, grass_spacing=grass_spacing,
                                                     terrain=terrain, terrain3d_addon=terrain3d_addon, terrain3d_material=terrain3d_material,
                                                     terrain3d_assets=terrain3d_assets, terrain3d_texture_ids=terrain3d_texture_ids,
                                                     terrain3d_collision=terrain3d_collision, water_script=water_script))
        if verify:
            r["godot_verification"] = verify_with_godot(out_path)          # embedded packages are verified in a temp copy: the host project is never opened
        r["next_step"] = ("In the Godot editor: let it import (or run a filesystem scan), then instance " + res_prefix + "World.tscn in your scene (or open " + res_prefix + "Preview.tscn for a quick look).")
        EXPORT_STATE["last"] = r
        return r
    try:
        if background:
            tid = TASKS.submit("export_to_godot", job)
            return {"ok": True, "task_id": tid, "status": TASKS.status(tid)["status"]}
        return job()
    except Exception as e:  # noqa: BLE001
        return _err(e)


def inspect_godot_project(godot_project: str) -> dict:
    """Look inside a Godot project before exporting into it: Terrain3D installed (version), the Terrain3DMaterials it uses (its own look, also
    embedded in scenes), water scripts that answer surface_at (they can draw our water), and a `suggestion` of export_to_godot arguments."""
    from exporters.godot.host_project import inspect_host_project
    try:
        return inspect_host_project(godot_project)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def get_export_status(task_id: str | None = None) -> dict:
    """Result of the last export, or of a background export task."""
    if task_id:
        return get_task_status(task_id)
    return {"ok": EXPORT_STATE["last"] is not None, "last_export": EXPORT_STATE["last"]}


TOOLS = [get_capabilities, create_world, set_world_style, generate_landscape, get_world_info, get_terrain_info, save_world, load_world, sculpt_terrain, raise_terrain, lower_terrain,
         smooth_terrain, flatten_terrain, apply_terrain_noise, create_mountain, create_mountain_range, create_valley, create_bowl, terrace_terrain, create_border_mountains, create_basin,
         create_plateau, create_cliff, apply_erosion, get_task_status, cancel_task, create_river, create_river_auto, create_lake, create_path, create_trail, create_climbable_mountain, analyze_traversability,
         scatter_vegetation, place_objects, modify_operation, set_operation_enabled, paint_terrain_material, inspect_terrain, inspect_region, analyze_elevation, analyze_slope,
         detect_terrain_issues, validate_world, get_world_preview, capture_world_view, create_checkpoint, rollback_to_checkpoint,
         compare_world_versions, undo, redo, list_operations, prepare_godot_export, inspect_godot_project, export_to_godot, get_export_status]
