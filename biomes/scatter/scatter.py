"""Vegetation scatter driven by altitude/slope/water-proximity rules. Deterministic per seed."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from biomes.masks.masks import cliff_distance, path_mask, slope_degrees, water_distance, water_mask
from terrain.heightfield.heightfield import Heightfield
from terrain.procedural.shapes import _poly_sdf

INSTANCE_COLS = ["x", "y", "z", "yaw", "radius", "height", "species"]  # y = up


@dataclass
class Species:
    name: str
    min_sep: float = 6.0
    density: float = 0.6                   # acceptance probability 0..1
    altitude: tuple[float, float] = (-1e9, 1e9)
    max_slope: float = 30.0                # degrees
    min_slope: float = 0.0                 # degrees (e.g. rocks prefer steeper ground)
    cliff_dist: tuple[float, float] = (0.0, 1e9)   # allowed distance (m) to cliff faces (slope >= 55 deg)
    water_dist: tuple[float, float] = (0.0, 1e9)   # allowed distance to water (m)
    scale: tuple[float, float] = (0.8, 1.2)
    radius: float = 2.0                    # base size in meters
    height: float = 8.0
    clusters: float = 0.0                  # 0..1: modulate density with low-freq noise


def _sample_many(hf: Heightfield, x: np.ndarray, z: np.ndarray) -> np.ndarray:
    fx = np.clip(x / hf.cell_size, 0, hf.nx - 1.001); fz = np.clip(z / hf.cell_size, 0, hf.ny - 1.001)
    x0, z0 = fx.astype(int), fz.astype(int); tx, tz = fx - x0, fz - z0
    d = hf.data
    return (d[z0, x0] * (1 - tx) * (1 - tz) + d[z0, x0 + 1] * tx * (1 - tz)
            + d[z0 + 1, x0] * (1 - tx) * tz + d[z0 + 1, x0 + 1] * tx * tz)


def scatter(hf: Heightfield, species: list[dict], seed: int = 0, region: list[tuple[float, float]] | None = None,
            exclusion: list[list[tuple[float, float]]] | None = None, _log=()):
    rng = np.random.default_rng(seed)
    wmask = water_mask(hf, _log)
    wdist = water_distance(hf, wmask)
    blocked = wmask | path_mask(hf, _log)
    slope = slope_degrees(hf)
    cdist = None
    out, counts, placed = [], {}, None
    for sid, sp_d in enumerate(species):
        sp = Species(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in sp_d.items()})
        sx, sz = hf.size
        n = max(int(sx / sp.min_sep), 1), max(int(sz / sp.min_sep), 1)
        gx, gz = np.meshgrid(np.arange(n[0]), np.arange(n[1]))
        x = ((gx.ravel() + rng.random(gx.size)) * sx / n[0]).astype(np.float64)
        z = ((gz.ravel() + rng.random(gz.size)) * sz / n[1]).astype(np.float64)
        accept_rnd = rng.random(x.size); scale = rng.uniform(*sp.scale, x.size); yaw = rng.uniform(0, 360, x.size)
        ci = np.clip((z / hf.cell_size).astype(int), 0, hf.ny - 1); cj = np.clip((x / hf.cell_size).astype(int), 0, hf.nx - 1)
        y = _sample_many(hf, x, z)
        ok = (y >= sp.altitude[0]) & (y <= sp.altitude[1]) & (slope[ci, cj] <= sp.max_slope) & ~blocked[ci, cj]
        ok &= (wdist[ci, cj] >= sp.water_dist[0]) & (wdist[ci, cj] <= sp.water_dist[1])
        ok &= slope[ci, cj] >= sp.min_slope
        if sp.cliff_dist != (0.0, 1e9):
            if cdist is None:
                cdist = cliff_distance(hf)
            ok &= (cdist[ci, cj] >= sp.cliff_dist[0]) & (cdist[ci, cj] <= sp.cliff_dist[1])
        dens = np.full(x.size, sp.density)
        if sp.clusters > 0:
            from terrain.procedural.noise import fbm
            nz = fbm(hf.nx, hf.ny, hf.cell_size, seed + 101 + sid, 1 / 120.0, 3)[ci, cj] * 0.5 + 0.5
            groves = np.clip((nz - 0.45) / 0.17, 0, 1); groves = groves * groves * (3 - 2 * groves)     # clumps with open meadows between
            dens = sp.density * ((1 - sp.clusters) + sp.clusters * 1.6 * groves)
        ok &= accept_rnd < dens
        if region:
            ok &= _poly_sdf(x.astype(np.float32), z.astype(np.float32), np.asarray(region, np.float32)) <= 0
        for poly in exclusion or []:
            ok &= _poly_sdf(x.astype(np.float32), z.astype(np.float32), np.asarray(poly, np.float32)) > 0
        pts = np.stack([x, z], -1)[ok]
        if placed is not None and len(pts):                   # keep clear of earlier species
            d, _ = cKDTree(placed).query(pts)
            keep = d >= (sp.min_sep + 0) * 0.5
            ok_idx = np.flatnonzero(ok)[keep]
        else:
            ok_idx = np.flatnonzero(ok)
        inst = np.stack([x[ok_idx], y[ok_idx], z[ok_idx], yaw[ok_idx], sp.radius * scale[ok_idx],
                         sp.height * scale[ok_idx], np.full(len(ok_idx), sid)], -1).astype(np.float32)
        out.append(inst); counts[sp.name] = int(len(inst))
        placed = inst[:, [0, 2]] if placed is None else np.vstack([placed, inst[:, [0, 2]]])
    inst = np.vstack(out) if out else np.zeros((0, 7), np.float32)
    return None, {"counts": counts, "total": int(len(inst)), "warnings": validate_vegetation(hf, inst, species, wmask),
                  "_instances": inst}


def validate_vegetation(hf: Heightfield, inst: np.ndarray, species: list[dict], wmask: np.ndarray, max_total: int = 200_000) -> list[dict]:
    w: list[dict] = []
    if len(inst):
        ci = np.clip((inst[:, 2] / hf.cell_size).astype(int), 0, hf.ny - 1); cj = np.clip((inst[:, 0] / hf.cell_size).astype(int), 0, hf.nx - 1)
        n_water = int(wmask[ci, cj].sum())
        if n_water:
            w.append({"severity": "error", "code": "veg_in_water", "message": f"{n_water} instances are inside water."})
        sl = slope_degrees(hf)[ci, cj]
        lim = np.array([s.get("max_slope", 30.0) for s in species])[inst[:, 6].astype(int)]
        bad = int((sl > lim + 1e-3).sum())
        if bad:
            w.append({"severity": "warning", "code": "veg_steep", "message": f"{bad} instances exceed their max slope."})
    if len(inst) > max_total:
        w.append({"severity": "warning", "code": "veg_too_many", "message": f"{len(inst)} instances; group into MultiMesh/density-by-chunk."})
    return w
