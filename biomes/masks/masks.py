"""Derived analysis maps: slope, water, path, distance-to-water. All computed from heightfield + op log results."""
from __future__ import annotations

import numpy as np
from scipy.ndimage import distance_transform_edt
from scipy.spatial import cKDTree

from terrain.heightfield.heightfield import Heightfield
from terrain.procedural.shapes import _poly_sdf


def slope_degrees(hf: Heightfield) -> np.ndarray:
    gy, gx = np.gradient(hf.data, hf.cell_size)
    return np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)


def _grid_xy(hf: Heightfield):
    c = hf.cell_size
    return np.meshgrid(np.arange(hf.nx) * c, np.arange(hf.ny) * c)


def water_mask(hf: Heightfield, log) -> np.ndarray:
    """True where a river channel or lake surface exists (from the operation results)."""
    mask = np.zeros(hf.data.shape, bool)
    X, Y = _grid_xy(hf)
    for op in log:
        r = op.result
        if not op.enabled or not r:
            continue
        if op.kind == "river":
            pts = np.asarray(r["points"])
            d, _ = cKDTree(pts).query(np.stack([X.ravel(), Y.ravel()], -1), distance_upper_bound=r["width"] / 2 + hf.cell_size)
            mask |= np.isfinite(d).reshape(mask.shape)
        elif op.kind == "lake":
            from features.curves.curve import sample_curve
            poly = r["polygon"]
            outline = sample_curve(poly + [poly[0]], hf.cell_size * 2).astype(np.float32)
            mask |= (_poly_sdf(X, Y, outline) <= 0) & (hf.data < r["level"] + 0.05)
    return mask


def path_mask(hf: Heightfield, log) -> np.ndarray:
    mask = np.zeros(hf.data.shape, bool)
    X, Y = _grid_xy(hf)
    for op in log:
        r = op.result
        if op.enabled and r and op.kind == "path":
            pts = np.asarray(r["points"])
            d, _ = cKDTree(pts).query(np.stack([X.ravel(), Y.ravel()], -1), distance_upper_bound=r["width"] / 2 + hf.cell_size)
            mask |= np.isfinite(d).reshape(mask.shape)
    return mask


def water_distance(hf: Heightfield, wmask: np.ndarray) -> np.ndarray:
    """Distance (m) to the nearest water cell; large constant if there is no water."""
    if not wmask.any():
        return np.full(wmask.shape, 1e6, np.float32)
    return (distance_transform_edt(~wmask) * hf.cell_size).astype(np.float32)


def cliff_distance(hf: Heightfield, min_slope_deg: float = 55.0) -> np.ndarray:
    """Distance (m) to the nearest cliff face (cells steeper than min_slope_deg); large if there are none."""
    steep = slope_degrees(hf) >= min_slope_deg
    if not steep.any():
        return np.full(steep.shape, 1e6, np.float32)
    return (distance_transform_edt(~steep) * hf.cell_size).astype(np.float32)
