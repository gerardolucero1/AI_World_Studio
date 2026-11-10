"""Seeded, vectorized gradient noise (deterministic across runs)."""
from __future__ import annotations

import numpy as np


def _perlin(nx: int, ny: int, freq: float, rng: np.random.Generator, cell: float) -> np.ndarray:
    gx, gy = int(np.ceil(nx * cell * freq)) + 2, int(np.ceil(ny * cell * freq)) + 2
    ang = rng.random((gy, gx)) * 2 * np.pi
    gvx, gvy = np.cos(ang), np.sin(ang)
    x = np.arange(nx) * cell * freq
    y = np.arange(ny) * cell * freq
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[None, :], (y - y0)[:, None]
    X0, Y0 = x0[None, :], y0[:, None]

    def dot(ix, iy, dx, dy):
        return gvx[iy, ix] * dx + gvy[iy, ix] * dy

    u = fx * fx * fx * (fx * (fx * 6 - 15) + 10)
    v = fy * fy * fy * (fy * (fy * 6 - 15) + 10)
    n00 = dot(X0, Y0, fx, fy); n10 = dot(X0 + 1, Y0, fx - 1, fy)
    n01 = dot(X0, Y0 + 1, fx, fy - 1); n11 = dot(X0 + 1, Y0 + 1, fx - 1, fy - 1)
    return ((n00 * (1 - u) + n10 * u) * (1 - v) + (n01 * (1 - u) + n11 * u) * v).astype(np.float32)


def fbm(nx: int, ny: int, cell_size: float, seed: int, base_freq: float = 1 / 300.0,
        octaves: int = 6, lacunarity: float = 2.0, gain: float = 0.5, ridged: bool = False) -> np.ndarray:
    """Fractal noise normalized to ~[-1, 1]. base_freq in cycles per meter."""
    rng = np.random.default_rng(seed)
    out = np.zeros((ny, nx), np.float32)
    amp, freq, total = 1.0, base_freq, 0.0
    for _ in range(octaves):
        n = _perlin(nx, ny, freq, rng, cell_size) * 1.4
        if ridged:
            n = 1.0 - 2.0 * np.abs(n)
        out += amp * n
        total += amp; amp *= gain; freq *= lacunarity
    return np.clip(out / total, -1, 1)
