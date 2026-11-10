"""Surface materials: rule-based weights (altitude/slope/moisture) + manual paint layers.
Material definition is separate from geometry: changing it never touches the heightfield."""
from __future__ import annotations

import numpy as np

from biomes.masks.masks import path_mask, slope_degrees, water_distance, water_mask
from terrain.heightfield.heightfield import Heightfield
from terrain.sculpt.brushes import falloff

MATERIALS = ["grass", "dirt", "rock", "snow", "sand", "mud", "wet_rock", "gravel"]
IDX = {m: i for i, m in enumerate(MATERIALS)}
COLORS = np.array([(.30, .48, .22), (.42, .32, .22), (.58, .52, .45), (.95, .95, .97),
                   (.80, .72, .50), (.28, .20, .14), (.30, .30, .33), (.55, .53, .50)], np.float32)


def smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def rule_weights(hf: Heightfield, log=(), snow_line: float | str | None = None, rock_slope=None) -> np.ndarray:
    """(ny,nx,8) normalized weights. Rules: rock by slope, snow above snow_line on gentle slopes,
    sand/mud/wet-rock near water, dirt on paths, grass elsewhere."""
    h = hf.data
    snow_line = hf.style.get("snow_line") if snow_line is None else snow_line      # None = auto (85% of max), 'off' = never
    rock_slope = tuple(rock_slope or hf.style.get("rock_slope") or (28.0, 42.0))
    slope = slope_degrees(hf)
    wm = water_mask(hf, log)
    wd = water_distance(hf, wm)
    top = float(h.max()) if snow_line in (None, "off") else snow_line / 0.85
    snow_line = 1e9 if snow_line == "off" else (0.85 * top if snow_line is None else snow_line)
    w = np.zeros(h.shape + (8,), np.float32)
    rock = smooth(*rock_slope, slope)
    snow = smooth(snow_line, snow_line + 0.1 * max(top, 1), h) * (1 - rock)
    near = 1 - smooth(1.0, 8.0, wd)                                 # moisture proxy
    sand = near * (1 - rock) * (slope < 12)
    w[..., IDX["rock"]] = rock
    w[..., IDX["snow"]] = snow
    w[..., IDX["sand"]] = sand * 0.7
    w[..., IDX["mud"]] = near * 0.3 * (1 - rock)
    w[..., IDX["wet_rock"]] = near * rock
    w[..., IDX["dirt"]] = path_mask(hf, log).astype(np.float32)
    w[..., IDX["grass"]] = np.clip(1 - w.sum(-1), 0, 1)
    return w / np.maximum(w.sum(-1, keepdims=True), 1e-6)


def paint_weights(hf: Heightfield, log) -> tuple[np.ndarray, np.ndarray]:
    """Replay `paint_material` ops: returns (paint (ny,nx,8), coverage (ny,nx))."""
    paint = np.zeros(hf.data.shape + (8,), np.float32)
    cov = np.zeros(hf.data.shape, np.float32)
    for op in log:
        if op.kind != "paint_material" or not op.enabled:
            continue
        p = op.params
        c = hf.cell_size
        for px, py in (p.get("points") or [(p["x"], p["y"])]):       # a stroke is a list of dabs
            y0, y1, x0, x1 = hf.world_to_rect(px, py, p["radius"])
            xs = (np.arange(x0, x1) * c - px)[None, :]; ys = (np.arange(y0, y1) * c - py)[:, None]
            w = falloff(np.hypot(xs, ys), p["radius"], p.get("hardness", 0.3)) * p.get("strength", 1.0)
            sub, sc = paint[y0:y1, x0:x1], cov[y0:y1, x0:x1]
            sub *= (1 - w)[..., None]                   # paint over previous paint
            sub[..., IDX[p["material"]]] += w
            sc[:] = np.clip(sc * (1 - w) + w, 0, 1)
    return paint, cov


def final_weights(hf: Heightfield, log=(), **kw) -> np.ndarray:
    base = rule_weights(hf, log, **kw)
    paint, cov = paint_weights(hf, log)
    out = base * (1 - cov[..., None]) + paint
    return out / np.maximum(out.sum(-1, keepdims=True), 1e-6)


def paint_material(hf: Heightfield, x: float = 0.0, y: float = 0.0, radius: float = 10.0, material: str = "grass",
                   strength: float = 1.0, hardness: float = 0.3, points=None):
    """Operation: recorded only; weights are derived by replaying the log (see paint_weights)."""
    if material not in IDX:
        raise ValueError(f"unknown material {material!r}; choose from {MATERIALS}")
    return None
