"""Plateau cliff walls as real geometry (not heightfield): extruded outline with strata ledges,
overhang cap, base flare and noise. The wall stands on the terrain (bottom row sampled from the heightfield)."""
from __future__ import annotations

import numpy as np

from features.curves.curve import sample_curve
from geometry.mesh_processing.mesh import Mesh
from terrain.heightfield.heightfield import Heightfield
from terrain.procedural.noise import fbm
from terrain.procedural import shapes


def build_cliff_wall(hf: Heightfield, polygon: list[tuple[float, float]], top: float, foot_wall: float,
                     rows: int = 36, strata: int = 6, ledge: float = 0.0, overhang: float = 0.0,
                     flare: float = 0.0, roughness: float = 0.0, seed: int = 0, embed: float = 0.3,
                     tile: float = 8.0) -> Mesh:
    """Closed ring of vertices (n per row) x (rows+1). Offsets are outward-only so the wall always
    covers the narrow heightfield ramp (`foot_wall` = ramp width used by the plateau op)."""
    outline = sample_curve(list(polygon) + [polygon[0]], hf.cell_size * 1.5, closed=False)[:-1]
    n = len(outline)
    area = 0.5 * np.sum(outline[:, 0] * np.roll(outline[:, 1], -1) - np.roll(outline[:, 0], -1) * outline[:, 1])
    tang = np.roll(outline, -1, 0) - np.roll(outline, 1, 0)
    tang /= np.maximum(np.linalg.norm(tang, axis=1, keepdims=True), 1e-9)
    out_n = np.stack([tang[:, 1], -tang[:, 0]], -1) * (1 if area > 0 else -1)   # outward normal in (x,z)

    t = (np.linspace(0, 1, rows + 1) ** 1.5)[:, None]                          # denser rows near the top
    base = foot_wall * np.minimum(1.0, t * 8.0)                                # hides the ramp behind rock
    saw = (t * strata) % 1.0
    ledges = ledge * (1 - saw) * (t > 0.02)                                    # stepped strata ledges
    cap = overhang * np.exp(-((t - 0.06) / 0.05) ** 2)                         # protruding caprock
    flr = flare * t ** 3
    noise = np.zeros((rows + 1, n), np.float32)
    if roughness > 0:
        nz = fbm(n, rows + 1, 1.0, seed, 0.12, 4)                              # (rows+1, n)
        col = fbm(n, 2, 1.0, seed + 1, 0.2, 3)[0][None, :]                     # vertical-column variation
        noise = (np.clip(nz * 0.6 + col * 0.4, -1, 1) * 0.5 + 0.5) * roughness * (t > 0.02)
    off = base + ledges + cap + flr + noise                                    # (rows+1, n), >= base >= 0
    pos = outline[None, :, :] + out_n[None, :, :] * off[:, :, None]            # (rows+1, n, 2)

    # heights: top -> ground at each vertex's own foot position
    foot = outline + out_n * (foot_wall + hf.cell_size)
    ground = np.array([hf.sample(float(x), float(z)) for x, z in foot], np.float32)
    ground = np.minimum(ground, top)
    y = top - t * (top - ground)[None, :]
    # bottom row sits on the actual terrain under it (no floating), slightly embedded
    bx, bz = pos[-1, :, 0], pos[-1, :, 1]
    y[-1] = np.array([hf.sample(float(a), float(b)) for a, b in zip(bx, bz)], np.float32) - embed

    verts = np.stack([pos[..., 0], y, pos[..., 1]], -1).reshape(-1, 3).astype(np.float32)
    idx = np.arange((rows + 1) * n, dtype=np.uint32).reshape(rows + 1, n)
    a, b = idx[:-1], np.roll(idx[:-1], -1, axis=1)
    c, d = idx[1:], np.roll(idx[1:], -1, axis=1)
    faces = np.stack([a, c, b, b, c, d], -1).reshape(-1, 3)
    seg = np.hypot(*np.diff(np.vstack([outline, outline[:1]]), axis=0).T)
    u = np.tile(np.concatenate([[0], np.cumsum(seg)[:-1]]) / tile, rows + 1)
    v = (top - y).reshape(-1) / tile
    mesh = Mesh(verts, faces.astype(np.uint32), np.stack([u, v], -1).astype(np.float32))
    # winding: make face normals point away from the plateau centre
    cen = verts[:, [0, 2]].mean(0)
    fn = mesh.face_normals(); fc = verts[mesh.faces].mean(1)
    if np.mean(np.einsum("ij,ij->i", fn[:, [0, 2]], fc[:, [0, 2]] - cen)) < 0:
        mesh.faces = mesh.faces[:, [0, 2, 1]].copy()
    return mesh


def validate_cliff(hf: Heightfield, mesh: Mesh, ring: int, tol: float = 1.0) -> list[dict]:
    """`ring` = vertices per row. Checks: degenerate faces, wall detached from terrain, wall too low."""
    out: list[dict] = []
    deg = mesh.degenerate_faces()
    if deg:
        out.append({"severity": "warning", "code": "cliff_degenerate", "message": f"{deg} degenerate triangles."})
    bottom = mesh.vertices[-ring:]
    gap = np.array([abs(v[1] - hf.sample(float(v[0]), float(v[2]))) for v in bottom])
    if gap.max() > tol:
        i = int(gap.argmax())
        out.append({"severity": "error", "code": "cliff_detached", "location": [float(bottom[i][0]), float(bottom[i][2])],
                    "message": f"Wall base is {gap.max():.1f} m from the terrain.", "suggestion": "Rebuild the cliff after changing terrain."})
    height = float(mesh.vertices[:ring, 1].mean() - bottom[:, 1].mean())
    if height < 1.0:
        out.append({"severity": "warning", "code": "cliff_low", "message": f"Wall only {height:.1f} m tall: plateau top is near ground level."})
    return out


def cliff_mesh_only(hf: Heightfield, polygon, height: float, wall_noise: float = 0.0, strata: int = 6, ledge: float = 1.0,
                    overhang: float = 1.5, flare: float = 3.0, roughness: float = 2.0, seed: int = 0) -> Mesh:
    """Rebuild just the wall mesh (used after loading a project; heightfield already has the plateau)."""
    return build_cliff_wall(hf, polygon, height, hf.cell_size * 1.5, strata=strata, ledge=ledge, overhang=overhang,
                            flare=flare, roughness=roughness, seed=seed)


def create_cliff_plateau(hf: Heightfield, polygon: list[tuple[float, float]], height: float, wall_noise: float = 0.0,
                         strata: int = 6, ledge: float = 1.0, overhang: float = 1.5, flare: float = 3.0,
                         roughness: float = 2.0, seed: int = 0):
    """Plateau top on the heightfield (narrow ramp) + independent wall mesh. Returns (rect, report)."""
    ramp = hf.cell_size * 1.5
    rect = shapes.plateau(hf, polygon, height, wall_width=ramp, wall_noise=0.0, seed=seed)
    mesh = build_cliff_wall(hf, polygon, height, ramp, strata=strata, ledge=ledge, overhang=overhang,
                            flare=flare, roughness=roughness, seed=seed)
    ring = len(mesh.vertices) // 37
    warnings = validate_cliff(hf, mesh, ring)
    # widen dirty rect to include wall footprint
    x, z = mesh.vertices[:, 0], mesh.vertices[:, 2]
    c = hf.cell_size
    rect = (min(rect[0], max(int(z.min() / c), 0)), max(rect[1], min(int(z.max() / c) + 2, hf.ny)),
            min(rect[2], max(int(x.min() / c), 0)), max(rect[3], min(int(x.max() / c) + 2, hf.nx)))
    return rect, {"top": height, "vertices": int(len(mesh.vertices)), "triangles": int(len(mesh.faces)),
                  "polygon": [list(p) for p in polygon], "warnings": warnings, "_mesh": mesh}
