"""Heightfield -> chunked mesh buffers. Chunks share border vertices so seams are watertight."""
from __future__ import annotations

import numpy as np

from terrain.heightfield.heightfield import Heightfield, Rect

CHUNK_CELLS = 64


def chunk_grid(hf: Heightfield, cells: int = CHUNK_CELLS) -> tuple[int, int]:
    return -(-(hf.nx - 1) // cells), -(-(hf.ny - 1) // cells)


def dirty_chunks(rect: Rect, hf: Heightfield, cells: int = CHUNK_CELLS) -> set[tuple[int, int]]:
    """Chunks whose vertices or normals (1-vertex margin) are affected by rect."""
    ncx, ncy = chunk_grid(hf, cells)
    y0, y1, x0, x1 = max(rect[0] - 1, 0), rect[1] + 1, max(rect[2] - 1, 0), rect[3] + 1
    out = set()
    for cy in range(y0 // cells, min((y1 - 1) // cells, ncy - 1) + 1):
        for cx in range(x0 // cells, min((x1 - 1) // cells, ncx - 1) + 1):
            out.add((cx, cy))
    return out


def build_chunk(hf: Heightfield, cx: int, cy: int, cells: int = CHUNK_CELLS) -> np.ndarray:
    """Interleaved float32 [px,py,pz,nx,ny,nz] per vertex; Y is up (height), grid row maps to Z."""
    x0, y0 = cx * cells, cy * cells
    x1, y1 = min(x0 + cells + 1, hf.nx), min(y0 + cells + 1, hf.ny)
    c = hf.cell_size
    h = hf.data[y0:y1, x0:x1]
    # normals from central differences using real neighbours (clamped at world edge)
    pad = np.pad(hf.data[max(y0 - 1, 0):min(y1 + 1, hf.ny), max(x0 - 1, 0):min(x1 + 1, hf.nx)],
                 ((1 if y0 == 0 else 0, 1 if y1 == hf.ny else 0), (1 if x0 == 0 else 0, 1 if x1 == hf.nx else 0)),
                 mode="edge")
    dzdx = (pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * c)
    dzdy = (pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * c)
    n = np.stack([-dzdx, np.ones_like(dzdx), -dzdy], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    xs = np.arange(x0, x1, dtype=np.float32) * c
    zs = np.arange(y0, y1, dtype=np.float32) * c
    px, pz = np.meshgrid(xs, zs)
    return np.concatenate([np.stack([px, h, pz], -1), n], -1).astype(np.float32)


def chunk_indices(w: int, h: int) -> np.ndarray:
    """Triangle indices (uint32) for a w x h vertex grid."""
    idx = np.arange(w * h, dtype=np.uint32).reshape(h, w)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]
    return np.stack([a, c, b, b, c, d], -1).reshape(-1)
