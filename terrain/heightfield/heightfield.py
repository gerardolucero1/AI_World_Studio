"""Heightfield: grid of float32 heights (meters). Row = Y(z) index, column = X index."""
from __future__ import annotations

import numpy as np

Rect = tuple[int, int, int, int]  # (y0, y1, x0, x1) half-open, in vertex indices


class Heightfield:
    def __init__(self, nx: int, ny: int, cell_size: float = 1.0, data: np.ndarray | None = None):
        self.cell_size = float(cell_size)
        self.style: dict = {}   # world-level look settings: snow_line (m | 'off' | None=auto), rock_slope (deg, deg)
        self.data = np.zeros((ny, nx), np.float32) if data is None else np.ascontiguousarray(data, np.float32)
        if self.data.shape != (ny, nx):
            raise ValueError("data shape mismatch")

    @property
    def nx(self) -> int:
        return self.data.shape[1]

    @property
    def ny(self) -> int:
        return self.data.shape[0]

    @property
    def size(self) -> tuple[float, float]:
        return (self.nx - 1) * self.cell_size, (self.ny - 1) * self.cell_size

    def sample(self, x: float, y: float) -> float:
        """Bilinear height at world coords (meters). Clamped to the grid."""
        fx = min(max(x / self.cell_size, 0.0), self.nx - 1.0)
        fy = min(max(y / self.cell_size, 0.0), self.ny - 1.0)
        x0, y0 = min(int(fx), self.nx - 2), min(int(fy), self.ny - 2)
        tx, ty = fx - x0, fy - y0
        d = self.data
        top = d[y0, x0] * (1 - tx) + d[y0, x0 + 1] * tx
        bot = d[y0 + 1, x0] * (1 - tx) + d[y0 + 1, x0 + 1] * tx
        return float(top * (1 - ty) + bot * ty)

    def world_to_rect(self, x: float, y: float, r: float) -> Rect:
        c = self.cell_size
        x0 = max(int((x - r) / c), 0)
        x1 = min(int((x + r) / c) + 2, self.nx)
        y0 = max(int((y - r) / c), 0)
        y1 = min(int((y + r) / c) + 2, self.ny)
        return y0, y1, x0, x1
