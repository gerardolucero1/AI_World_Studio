"""Walkable-area NavigationMesh built in Python (no Godot bake needed): slope-limited grid of triangles.
Triangles share vertices, so Godot connects them. Winding is clockwise seen from above (Godot accepts either).
A triangle is walkable when its three edges are within the grade limit and none of its corners is in water, so narrow trails with steep banks stay connected."""
from __future__ import annotations

import numpy as np

from biomes.masks.masks import path_mask, water_mask
from terrain.heightfield.heightfield import Heightfield


def build_navmesh(hf: Heightfield, log, cell_m: float = 4.0, max_slope_deg: float = 40.0, max_tris: int = 260_000):
    """Returns (vertices (N,3) float32, triangles (M,3) int, stats)."""
    s = max(1, int(round(cell_m / hf.cell_size)))
    while ((hf.ny - 1) // s) * ((hf.nx - 1) // s) * 2 > max_tris:
        s += 1
    h = hf.data[::s, ::s].astype(np.float64)
    ny, nx = h.shape
    step = hf.cell_size * s
    wet = water_mask(hf, log)[::s, ::s]
    trail = path_mask(hf, log)[::s, ::s]                                     # carved trails are walkable by construction (grade-limited bed)
    g = np.tan(np.radians(max_slope_deg))
    ok_x = np.abs(np.diff(h, axis=1)) / step <= g                              # (i,j)-(i,j+1)        shape (ny, nx-1)
    ok_z = np.abs(np.diff(h, axis=0)) / step <= g                              # (i,j)-(i+1,j)        shape (ny-1, nx)
    dh1 = np.abs(h[1:, 1:] - h[:-1, :-1]); dh2 = np.abs(h[1:, :-1] - h[:-1, 1:])  # diagonals per cell   shape (ny-1, nx-1)
    use1 = dh1 <= dh2                                                          # split along the flatter diagonal
    ok_d1 = dh1 / (step * 2 ** 0.5) <= g; ok_d2 = dh2 / (step * 2 ** 0.5) <= g
    w00, w01, w10, w11 = wet[:-1, :-1], wet[:-1, 1:], wet[1:, :-1], wet[1:, 1:]
    top, bottom = ok_x[:-1, :], ok_x[1:, :]; left, right = ok_z[:, :-1], ok_z[:, 1:]
    idx = np.arange(ny * nx).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]        # (i,j) (i,j+1) (i+1,j+1) (i+1,j): clockwise from above
    tris = []
    # diagonal a-c: triangles (a,b,c) and (a,c,d)
    p00, p01, p10, p11 = trail[:-1, :-1], trail[:-1, 1:], trail[1:, :-1], trail[1:, 1:]
    t1 = use1 & ((top & right & ok_d1) | (p00 & p01 & p11)) & ~w00 & ~w01 & ~w11
    t2 = use1 & ((left & bottom & ok_d1) | (p00 & p11 & p10)) & ~w00 & ~w11 & ~w10
    # diagonal b-d: triangles (a,b,d) and (b,c,d)
    t3 = ~use1 & ((top & left & ok_d2) | (p00 & p01 & p10)) & ~w00 & ~w01 & ~w10
    t4 = ~use1 & ((right & bottom & ok_d2) | (p01 & p11 & p10)) & ~w01 & ~w11 & ~w10
    for m, tri in ((t1, (a, b, c)), (t2, (a, c, d)), (t3, (a, b, d)), (t4, (b, c, d))):
        tris.append(np.stack([tri[0][m], tri[1][m], tri[2][m]], -1))
    tris = np.concatenate(tris)
    used = np.unique(tris)
    remap = -np.ones(ny * nx, np.int64); remap[used] = np.arange(len(used))
    X, Z = np.meshgrid(np.arange(nx) * step, np.arange(ny) * step)
    verts = np.stack([X.ravel()[used], h.ravel()[used] + 0.05, Z.ravel()[used]], -1).astype(np.float32)
    total = (ny - 1) * (nx - 1) * 2
    return verts, remap[tris], {"cell_m": step, "polygons": int(len(tris)), "vertices": int(len(verts)), "max_slope_deg": max_slope_deg,
                                "walkable_fraction": float(len(tris) / max(total, 1))}


def navmesh_tres(verts: np.ndarray, polys: np.ndarray, agent_radius: float = 0.5) -> str:
    v = ", ".join(f"{x:.2f}" for x in verts.reshape(-1))
    ps = ", ".join("PackedInt32Array(" + ", ".join(map(str, p)) + ")" for p in polys.tolist())
    return (f'[gd_resource type="NavigationMesh" format=3]\n\n[resource]\nvertices = PackedVector3Array({v})\npolygons = [{ps}]\n'
            f"agent_radius = {agent_radius}\n")
