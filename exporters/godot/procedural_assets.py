"""Procedural stylized props (round trees, pines, bushes, rocks) as vertex-coloured meshes.
Every mesh is normalised: base at y=0, height 1, horizontal radius 0.5 -> instance scale = (diameter, height, diameter)."""
from __future__ import annotations

import numpy as np


def _icosphere(subdiv: int):
    t = (1 + 5 ** 0.5) / 2
    v = np.array([(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
                  (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)], np.float64)
    f = np.array([(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
                  (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)])
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    for _ in range(subdiv):
        cache, verts, faces = {}, list(v), []

        def mid(a, b):
            k = (min(a, b), max(a, b))
            if k not in cache:
                m = (verts[a] + verts[b]) / 2; verts.append(m / np.linalg.norm(m)); cache[k] = len(verts) - 1
            return cache[k]
        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            faces += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        v, f = np.array(verts), np.array(faces)
    return v, f


def _vnoise(p: np.ndarray, seed: int) -> np.ndarray:
    """Cheap deterministic 3D value noise in [0,1] for an (N,3) array."""
    rng = np.random.default_rng(seed)
    tab = rng.random(4096)
    i = np.floor(p).astype(np.int64); fr = p - i; fr = fr * fr * (3 - 2 * fr)

    def h(ix, iy, iz):
        return tab[(ix * 73856093 ^ iy * 19349663 ^ iz * 83492791) & 4095]
    out = 0.0
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                w = (fr[:, 0] if dx else 1 - fr[:, 0]) * (fr[:, 1] if dy else 1 - fr[:, 1]) * (fr[:, 2] if dz else 1 - fr[:, 2])
                out = out + w * h(i[:, 0] + dx, i[:, 1] + dy, i[:, 2] + dz)
    return out


class _Builder:
    def __init__(self):
        self.p, self.n, self.c, self.f = [], [], [], []
        self.count = 0

    def add(self, pos, nrm, col, faces):
        self.p.append(pos); self.n.append(nrm); self.c.append(col); self.f.append(np.asarray(faces) + self.count); self.count += len(pos)

    def blob(self, center, radii, seed, subdiv=2, noise=0.18, flat=False, col_fn=None, normal_center=None, normal_mix=0.75):
        v, f = _icosphere(subdiv)
        d = v.copy()
        r = 1 + noise * (_vnoise(d * 2.3 + seed * 7.1, seed) - 0.5) * 2
        pos = d * r[:, None] * np.asarray(radii) + np.asarray(center)
        if flat:
            pos, f = pos[f].reshape(-1, 3), np.arange(len(f) * 3).reshape(-1, 3)
            fn = np.cross(pos[f[:, 1]] - pos[f[:, 0]], pos[f[:, 2]] - pos[f[:, 0]]); fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-9)
            nrm = np.repeat(fn, 3, axis=0)
        else:
            sph = pos - (np.asarray(normal_center) if normal_center is not None else np.asarray(center))
            sph /= np.maximum(np.linalg.norm(sph, axis=1, keepdims=True), 1e-9)
            nrm = sph
        col = col_fn(pos, nrm)
        self.add(pos, nrm, col, f)

    def cylinder(self, base, top, r0, r1, sides, col0, col1, cap=False):
        ang = np.linspace(0, 2 * np.pi, sides, endpoint=False)
        ring = np.stack([np.cos(ang), np.zeros(sides), np.sin(ang)], -1)
        pos = np.vstack([ring * r0 + base, ring * r1 + top]); nrm = np.vstack([ring, ring])
        nrm[:, 1] = (r0 - r1) / max(np.linalg.norm(np.asarray(top) - np.asarray(base)), 1e-6)
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
        col = np.vstack([np.tile(col0, (sides, 1)), np.tile(col1, (sides, 1))])
        f = []
        for i in range(sides):
            j = (i + 1) % sides
            f += [(i, sides + i, j), (j, sides + i, sides + j)]
        self.add(pos, nrm, col, f)

    def build(self):
        pos = np.vstack(self.p); nrm = np.vstack(self.n); col = np.vstack(self.c); idx = np.vstack(self.f)
        mn, mx = pos.min(0), pos.max(0)
        rad = max(np.hypot(pos[:, 0], pos[:, 2]).max(), 1e-6)
        pos = (pos - np.array([0, mn[1], 0])) * np.array([0.5 / rad, 1.0 / max(mx[1] - mn[1], 1e-6), 0.5 / rad])
        return dict(positions=pos.astype(np.float32), normals=nrm.astype(np.float32), colors=np.clip(col, 0, 1).astype(np.float32),
                    indices=idx.astype(np.uint32))


def _foliage_colors(dark, mid, light, center_y, span):
    def fn(pos, nrm):
        t = np.clip((pos[:, 1] - (center_y - span)) / (2 * span), 0, 1)[:, None] * 0.55 + np.clip(nrm[:, 1] * 0.5 + 0.5, 0, 1)[:, None] * 0.45
        lo = np.asarray(dark) * (1 - np.clip(t * 2, 0, 1)) + np.asarray(mid) * np.clip(t * 2, 0, 1)
        return lo * (1 - np.clip(t * 2 - 1, 0, 1)) + np.asarray(light) * np.clip(t * 2 - 1, 0, 1)
    return fn


TRUNK = (0.40, 0.28, 0.17)


def round_tree(seed: int = 0):
    rng = np.random.default_rng(seed)
    b = _Builder()
    th = 2.4
    b.cylinder((0, 0, 0), (0.05 * rng.uniform(-1, 1), th, 0), 0.42, 0.26, 7, TRUNK, TRUNK)
    b.cylinder((0, th * 0.55, 0), (0.7 * rng.uniform(-1, 1), th * 0.95, 0.7 * rng.uniform(-1, 1)), 0.16, 0.1, 5, TRUNK, TRUNK)
    colf = _foliage_colors((0.12, 0.38, 0.10), (0.27, 0.58, 0.15), (0.55, 0.80, 0.22), 4.4, 2.4)
    centre = np.array([0.0, 4.2, 0.0])
    for k in range(6):
        a = rng.uniform(0, 2 * np.pi); rr = rng.uniform(0.2, 1.3) if k else 0
        c = centre + np.array([np.cos(a) * rr, rng.uniform(-0.5, 0.9) + (0.6 if k == 0 else 0), np.sin(a) * rr])
        b.blob(c, np.array([1.5, 1.15, 1.5]) * rng.uniform(0.85, 1.15), seed * 13 + k, 2, 0.20, False, colf, centre, 0.7)
    return b.build()


def pine_tree(seed: int = 0):
    rng = np.random.default_rng(seed + 100)
    b = _Builder()
    b.cylinder((0, 0, 0), (0, 1.8, 0), 0.34, 0.22, 7, TRUNK, TRUNK)
    tiers = 5
    for i in range(tiers):
        y0 = 1.5 + i * 1.5; r = 1.9 * (1 - i / (tiers + 0.6)); h = 2.6
        ang = np.linspace(0, 2 * np.pi, 9, endpoint=False) + rng.uniform(0, 1)
        rim = np.stack([np.cos(ang) * r * rng.uniform(0.9, 1.1, 9), np.full(9, y0), np.sin(ang) * r * rng.uniform(0.9, 1.1, 9)], -1)
        tip = np.array([[0.0, y0 + h, 0.0]])
        pos = np.vstack([tip, rim]); nrm = np.vstack([[0, 1, 0], np.stack([np.cos(ang), np.full(9, 0.55), np.sin(ang)], -1)])
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
        t = i / tiers
        top = np.array([0.20, 0.52, 0.22]) * (0.8 + 0.4 * t); base = np.array([0.08, 0.30, 0.16]) * (0.85 + 0.3 * t)
        col = np.vstack([top, np.tile(base, (9, 1))])
        f = [(0, 1 + (j + 1) % 9, 1 + j) for j in range(9)]
        b.add(pos, nrm, col, f)
    return b.build()


def bush(seed: int = 0):
    rng = np.random.default_rng(seed + 200)
    b = _Builder()
    colf = _foliage_colors((0.15, 0.42, 0.12), (0.30, 0.62, 0.16), (0.58, 0.82, 0.26), 0.6, 0.9)
    for k in range(4):
        a = rng.uniform(0, 2 * np.pi); rr = rng.uniform(0.0, 0.7) if k else 0
        b.blob((np.cos(a) * rr, 0.55, np.sin(a) * rr), np.array([0.8, 0.65, 0.8]) * rng.uniform(0.85, 1.15), seed * 7 + k, 2, 0.22, False, colf, (0, 0.5, 0), 0.7)
    return b.build()


def rock(seed: int = 0):
    b = _Builder()

    def colf(pos, nrm):
        up = np.clip(nrm[:, 1] * 0.5 + 0.5, 0, 1)[:, None]
        return np.asarray((0.38, 0.41, 0.50)) * (1 - up) + np.asarray((0.66, 0.66, 0.66)) * up
    b.blob((0, 0.2, 0), np.array([1.0, 0.7, 0.85]), seed + 5, 1, 0.38, True, colf)
    # keep a flat base
    b.p[0][:, 1] = np.maximum(b.p[0][:, 1], 0.0)
    return b.build()


def grass_tuft(seed: int = 0, blades: int = 8):
    """A tuft of tapered blades (vertex colour = grey ramp base->tip, multiplied by the terrain's grass tint in the shader)."""
    rng = np.random.default_rng(seed + 300)
    b = _Builder()
    for _ in range(blades):
        ang = rng.uniform(0, 2 * np.pi); r0 = rng.uniform(0.0, 0.30)
        base = np.array([np.cos(ang) * r0, 0.0, np.sin(ang) * r0])
        face = ang + rng.uniform(-0.7, 0.7)
        side = np.array([np.cos(face), 0.0, np.sin(face)])
        out = np.array([np.cos(ang), 0.0, np.sin(ang)])
        hk = rng.uniform(0.7, 1.0); lean = rng.uniform(0.08, 0.38)
        ys = np.array([0.0, 0.42, 0.78, 1.0]) * hk
        ws = np.array([0.15, 0.115, 0.06, 0.0])
        tone = rng.uniform(0.9, 1.1)
        ramp = np.array([0.50, 0.78, 0.98, 1.08]) * tone
        pos, col = [], []
        for k in range(3):
            c = base + out * lean * (ys[k] / hk) ** 2 + np.array([0, ys[k], 0])
            pos += [c - side * ws[k] / 2, c + side * ws[k] / 2]; col += [np.full(3, ramp[k]), np.full(3, ramp[k])]
        tip = base + out * lean + np.array([0, ys[3], 0])
        pos.append(tip); col.append(np.full(3, ramp[3]))
        pos = np.array(pos); col = np.array(col)
        faces = [(0, 1, 2), (1, 3, 2), (2, 3, 4), (3, 5, 4), (4, 5, 6)]
        b.add(pos, np.tile([0.0, 1.0, 0.0], (len(pos), 1)), col, faces)
    return b.build()


PROTOTYPES = {
    "grass_tuft": lambda: grass_tuft(1),
    "round_tree_a": lambda: round_tree(1), "round_tree_b": lambda: round_tree(2), "round_tree_c": lambda: round_tree(3),
    "pine_a": lambda: pine_tree(1), "pine_b": lambda: pine_tree(2),
    "bush_a": lambda: bush(1), "bush_b": lambda: bush(2),
    "rock_a": lambda: rock(1), "rock_b": lambda: rock(2), "rock_c": lambda: rock(3), "rock_d": lambda: rock(4),
}
