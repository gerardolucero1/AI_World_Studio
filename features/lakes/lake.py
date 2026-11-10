from __future__ import annotations

import numpy as np

from features.curves.curve import sample_curve, smoothstep
from terrain.heightfield.heightfield import Heightfield
from terrain.procedural.shapes import _grid, _poly_rect, _poly_sdf


def create_lake(hf: Heightfield, polygon: list[tuple[float, float]], depth: float = 4.0, level: float | None = None,
                shore_width: float = 15.0, rim_margin: float = 0.2):
    """Carve a basin inside `polygon`. Default water level = lowest rim height - rim_margin (so no spill/leak)."""
    outline = sample_curve(polygon + [polygon[0]], hf.cell_size * 2)
    rect = _poly_rect(hf, outline, shore_width * 1.5)
    X, Y = _grid(hf, rect)
    sdf = _poly_sdf(X, Y, outline.astype(np.float32))
    cur = hf.data[rect[0]:rect[1], rect[2]:rect[3]]
    rim = (sdf > -hf.cell_size * 1.5) & (sdf < hf.cell_size * 1.5)
    warnings = []
    rim_min = float(cur[rim].min()) if rim.any() else float(cur.min())  # original terrain at the shoreline
    if level is None:
        level = float(cur[rim].min()) - rim_margin if rim.any() else float(cur.min())
    inner = np.clip(-sdf / shore_width, 0, 1)                 # 0 at shoreline -> 1 deep inside
    floor = level - depth * smoothstep(inner) - 0.05
    outer = smoothstep(1 - np.clip(sdf / shore_width, 0, 1))   # soft blend outside the shoreline
    target = np.where(sdf <= 0, floor, level * outer + cur * (1 - outer))
    new = np.where(sdf <= 0, np.minimum(cur, floor), np.minimum(cur, target))
    cur[:] = new.astype(np.float32)
    # validate: rim terrain must stay at/above water level, no dry cells inside beyond the shore
    margin = rim_min - level
    if margin < -0.05:
        warnings.append({"severity": "error", "code": "lake_leak", "message": f"Lowest rim is {-margin:.2f} m below water level: water would spill.",
                         "suggestion": "Raise the rim or lower the water level."})
    dry = int(((sdf < -hf.cell_size * 2) & (cur > level)).sum())
    if dry:
        warnings.append({"severity": "warning", "code": "lake_dry_cells", "message": f"{dry} cells inside the lake are above water."})
    return rect, {"level": level, "depth": depth, "area": float((sdf <= 0).sum() * hf.cell_size ** 2),
                  "polygon": [list(p) for p in polygon], "warnings": warnings}
