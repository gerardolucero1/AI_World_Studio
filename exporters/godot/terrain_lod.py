"""Terrain chunk meshes with LOD levels and skirts (hide cracks between different LODs)."""
from __future__ import annotations

import numpy as np

from terrain.chunks.mesh import build_chunk, chunk_indices
from terrain.heightfield.heightfield import Heightfield


def pick_chunk_cells(hf: Heightfield) -> int:
    for c in (64, 32, 16, 8, 4):
        if (hf.nx - 1) % c == 0 and (hf.ny - 1) % c == 0:
            return c
    raise ValueError(f"Resolution {hf.nx}x{hf.ny}: (vertices-1) must be divisible by 4 to split into chunks")


def lod_steps_for(cells: int, wanted=(1, 2, 4, 8)) -> list[int]:
    return [s for s in wanted if cells % s == 0 and cells // s >= 4]


def chunk_mesh(hf: Heightfield, cx: int, cy: int, cells: int, step: int = 1, skirt: bool = True):
    """Returns positions (N,3), normals (N,3), uvs (N,2), indices (M*3,) uint32. Y up; Z = grid row."""
    d = build_chunk(hf, cx, cy, cells)[::step, ::step]
    h, w = d.shape[:2]
    pos = d[..., :3].reshape(-1, 3).copy(); nrm = d[..., 3:].reshape(-1, 3).copy()
    idx = chunk_indices(w, h)
    if skirt:
        depth = hf.cell_size * step * 1.5 + 0.5
        grid = np.arange(w * h, dtype=np.uint32).reshape(h, w)
        extra_p, extra_n, extra_i, off = [], [], [], w * h
        for e, outward in ((grid[0, :], (0, -1)), (grid[-1, :], (0, 1)), (grid[:, 0], (-1, 0)), (grid[:, -1], (1, 0))):
            n = len(e)
            sp = pos[e].copy(); sp[:, 1] -= depth
            s = np.arange(off, off + n, dtype=np.uint32); off += n
            tris = np.concatenate([np.stack([e[:-1], s[:-1], e[1:]], -1), np.stack([e[1:], s[:-1], s[1:]], -1)])
            allp = np.vstack([pos, *extra_p, sp])
            t0 = tris[0]
            nrm0 = np.cross(allp[t0[1]] - allp[t0[0]], allp[t0[2]] - allp[t0[0]])
            if nrm0[0] * outward[0] + nrm0[2] * outward[1] < 0:      # make skirt faces point outward
                tris = tris[:, [0, 2, 1]]
            extra_p.append(sp); extra_n.append(nrm[e]); extra_i.append(tris.reshape(-1))
        pos = np.vstack([pos, *extra_p]); nrm = np.vstack([nrm, *extra_n]); idx = np.concatenate([idx, *extra_i])
    sx, sz = hf.size
    uv = np.stack([pos[:, 0] / sx, pos[:, 2] / sz], -1)
    return pos.astype(np.float32), nrm.astype(np.float32), uv.astype(np.float32), idx.astype(np.uint32)
