"""World validator: findings with severity, location, explanation and suggested fix (plan §12)."""
from __future__ import annotations

import numpy as np

from biomes.masks.masks import slope_degrees, water_mask
from core.world.session import WorldSession


def _f(severity, code, message, suggestion="", location=None, layer="terrain"):
    d = {"severity": severity, "code": code, "layer": layer, "message": message, "suggestion": suggestion}
    if location is not None:
        d["location"] = [round(float(location[0]), 1), round(float(location[1]), 1)]
    return d


def validate_terrain(s: WorldSession) -> list[dict]:
    hf, out = s.hf, []
    if not np.isfinite(hf.data).all():
        out.append(_f("error", "terrain_nan", "Heightfield contains NaN/Inf.", "Rollback to a checkpoint."))
        return out
    slope = slope_degrees(hf)
    steep = slope > 70
    if steep.mean() > 0.002:
        iy, ix = np.unravel_index(slope.argmax(), slope.shape)
        out.append(_f("warning", "extreme_slope", f"{steep.mean():.1%} of cells exceed 70° (max {slope.max():.0f}°).",
                      "Smooth the area or build the wall as cliff geometry.", (ix * hf.cell_size, iy * hf.cell_size)))
    jump = max(np.abs(np.diff(hf.data, axis=0)).max(), np.abs(np.diff(hf.data, axis=1)).max())
    if jump > 8 * hf.cell_size:
        out.append(_f("warning", "discontinuity", f"Height jump of {jump:.1f} m between neighbouring cells.", "Smooth or increase resolution."))
    if hf.cell_size > 4:
        out.append(_f("info", "low_resolution", f"Cell size {hf.cell_size:.1f} m is coarse for player-level views."))
    flat = float((slope < 1.0).mean())
    if flat > 0.6:
        out.append(_f("info", "mostly_flat", f"{flat:.0%} of the terrain is almost flat.", "Add noise, hills or ranges."))
    return out


def validate_world(s: WorldSession) -> dict:
    with s.lock:
        findings = validate_terrain(s)
        for op in s.op_log:
            if not op.enabled or not op.result:
                continue
            for w in op.result.get("warnings", []):
                f = dict(w); f.setdefault("layer", op.kind); f["source_operation"] = op.kind
                f.setdefault("suggestion", ""); findings.append(f)
        wm = water_mask(s.hf, s.op_log)
        for op in s.op_log:
            if op.kind == "path" and op.result:   # path crossing water
                from scipy.spatial import cKDTree  # noqa: F401
                pts = np.asarray(op.result["points"]); ci = np.clip((pts[:, 1] / s.hf.cell_size).astype(int), 0, s.hf.ny - 1)
                cj = np.clip((pts[:, 0] / s.hf.cell_size).astype(int), 0, s.hf.nx - 1)
                n = int(wm[ci, cj].sum())
                if n:
                    findings.append(_f("warning", "path_in_water", f"{n} path samples cross water.", "Add a bridge or reroute.", layer="path"))
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))
    return {"ok": not any(f["severity"] == "error" for f in findings), "counts": {k: sum(f["severity"] == k for f in findings) for k in order},
            "findings": findings}
