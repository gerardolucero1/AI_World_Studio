"""Approximate, deterministic, vectorized erosion (artistic control over simulation accuracy)."""
from __future__ import annotations

import numpy as np

from terrain.heightfield.heightfield import Heightfield

_N8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def thermal(hf: Heightfield, iterations: int = 30, talus: float = 0.7, rate: float = 0.4) -> None:
    """Move material downhill where slope exceeds talus (tan of angle). Mass-conserving."""
    h = hf.data
    c = hf.cell_size
    for _ in range(iterations):
        delta = np.zeros_like(h)
        p = np.pad(h, 1, mode="edge")
        for dy, dx in _N8:
            nb = p[1 + dy:1 + dy + h.shape[0], 1 + dx:1 + dx + h.shape[1]]
            dist = c * (1.414 if dy and dx else 1.0)
            diff = h - nb
            move = np.where(diff / dist > talus, (diff - talus * dist) * rate / 8, 0).astype(np.float32)
            delta -= move
            # material goes to the neighbour: shift move back by (dy, dx)
            shifted = np.zeros_like(h)
            ys, xs = slice(max(dy, 0), h.shape[0] + min(dy, 0)), slice(max(dx, 0), h.shape[1] + min(dx, 0))
            yd, xd = slice(max(-dy, 0), h.shape[0] + min(-dy, 0)), slice(max(-dx, 0), h.shape[1] + min(-dx, 0))
            shifted[ys, xs] = move[yd, xd]
            delta += shifted
        h += delta


def hydraulic(hf: Heightfield, iterations: int = 40, rain: float = 0.01, erode: float = 0.3,
              deposit: float = 0.3, evaporation: float = 0.02, capacity: float = 4.0, smooth: float = 0.0) -> None:
    """Grid-based water/sediment model: rain -> flow downhill -> erode where fast, deposit where slow.
    Produces drainage channels and sediment fans. Approximate; not mass-exact at borders."""
    h = hf.data
    water = np.zeros_like(h); sed = np.zeros_like(h)
    c = hf.cell_size
    for _ in range(iterations):
        water += rain
        p = np.pad(h + water, 1, mode="edge")
        # outflow fractions towards lower neighbours
        drops = []
        for dy, dx in _N8:
            nb = p[1 + dy:1 + dy + h.shape[0], 1 + dx:1 + dx + h.shape[1]]
            drops.append(np.maximum(h + water - nb, 0) / (c * (1.414 if dy and dx else 1.0)))
        drops = np.stack(drops)
        tot = drops.sum(0)
        frac = np.where(tot > 0, drops / np.maximum(tot, 1e-9), 0)
        speed = np.minimum(tot, 2.0)                      # steepness proxy for flow speed
        cap = capacity * speed * water
        d_e = np.where(sed < cap, (cap - sed) * erode, (cap - sed) * deposit)  # negative => deposit
        h -= d_e.astype(np.float32); sed += d_e
        # transport water and sediment together
        move_frac = np.minimum(tot * 0.5, 1.0)
        new_w = water * (1 - move_frac); new_s = sed * (1 - move_frac)
        for k, (dy, dx) in enumerate(_N8):
            fw = water * move_frac * frac[k]; fs = sed * move_frac * frac[k]
            ys, xs = slice(max(dy, 0), h.shape[0] + min(dy, 0)), slice(max(dx, 0), h.shape[1] + min(dx, 0))
            yd, xd = slice(max(-dy, 0), h.shape[0] + min(-dy, 0)), slice(max(-dx, 0), h.shape[1] + min(-dx, 0))
            new_w[ys, xs] += fw[yd, xd]; new_s[ys, xs] += fs[yd, xd]
        water, sed = new_w * (1 - evaporation), new_s
        h += (sed * evaporation * 0.5).astype(np.float32)  # deposit a bit on evaporation
        sed *= (1 - evaporation * 0.5)
    if smooth > 0:
        from scipy.ndimage import gaussian_filter
        hf.data[:] = gaussian_filter(h, smooth).astype(np.float32)
