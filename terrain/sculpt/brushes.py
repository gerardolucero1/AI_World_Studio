"""Sculpt brushes operating in-place on a Heightfield. Each dab returns the dirty Rect."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from scipy.ndimage import uniform_filter

from terrain.heightfield.heightfield import Heightfield, Rect


class Tool(str, Enum):
    RAISE = "raise"
    LOWER = "lower"
    SMOOTH = "smooth"
    FLATTEN = "flatten"


@dataclass
class BrushParams:
    radius: float = 20.0      # meters
    strength: float = 0.5     # 0..1 (raise/lower: meters per dab = strength * strength_scale)
    hardness: float = 0.0     # 0 = full smooth falloff, 1 = hard edge
    target_height: float = 0.0
    strength_scale: float = 2.0


def falloff(dist: np.ndarray, radius: float, hardness: float) -> np.ndarray:
    t = np.clip(dist / radius, 0.0, 1.0)
    h = min(max(hardness, 0.0), 0.999)
    u = np.clip((t - h) / (1.0 - h), 0.0, 1.0)
    return (1.0 - u * u * (3.0 - 2.0 * u)).astype(np.float32)  # smoothstep


def apply_dab(hf: Heightfield, tool: Tool, x: float, y: float, p: BrushParams) -> Rect | None:
    y0, y1, x0, x1 = hf.world_to_rect(x, y, p.radius)
    if y1 <= y0 or x1 <= x0:
        return None
    c = hf.cell_size
    xs = (np.arange(x0, x1, dtype=np.float32) * c - x)[None, :]
    ys = (np.arange(y0, y1, dtype=np.float32) * c - y)[:, None]
    w = falloff(np.hypot(xs, ys), p.radius, p.hardness)
    if not w.any():
        return None
    patch = hf.data[y0:y1, x0:x1]
    if tool is Tool.RAISE:
        patch += w * p.strength * p.strength_scale
    elif tool is Tool.LOWER:
        patch -= w * p.strength * p.strength_scale
    elif tool is Tool.FLATTEN:
        patch += (np.float32(p.target_height) - patch) * w * min(p.strength, 1.0)
    elif tool is Tool.SMOOTH:
        m = 2  # margin so edges blur against real neighbours
        ya, yb = max(y0 - m, 0), min(y1 + m, hf.ny)
        xa, xb = max(x0 - m, 0), min(x1 + m, hf.nx)
        blurred = uniform_filter(hf.data[ya:yb, xa:xb], size=5, mode="nearest")
        blurred = blurred[y0 - ya:y1 - ya, x0 - xa:x1 - xa]
        patch += (blurred - patch) * w * min(p.strength, 1.0)
    return y0, y1, x0, x1


def union(a: Rect | None, b: Rect | None) -> Rect | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3])
