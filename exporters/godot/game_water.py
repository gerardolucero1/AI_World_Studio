"""Water as data for a game's own water system (format of Wicktale's scripts/world/water.gd, generic enough for others):

  water.json   {"rivers": [{"name", "pts": [[x, y, z, left, right, fall], ...], "falls": [...]}],
                "lakes":  [{"name", "level", "x0", "z0", "cell", "cells": [[i, j], ...]}]}
               river samples every RIVER_STEP m: y = water surface, left/right = how far the water reaches each side (measured on the
               terrain, a little under the bank), fall 0..1 where it drops. Each fall = {"start", "end", "dt", "pool": [x, y, z, width],
               "cols": [{"o": offset across, "pts": [[x, y, z, speed, foam, in_air], ...]}], "splashes": [[x, y, z, strength], ...]}:
               columns of water simulated over the real ground (rows are moments in time), as the game's generator does.
  surface.r32  float32 water surface every SURF_CELL m from the world origin (rows north->south), NO_WATER where there is none:
               lets the player wade/swim (surface_at).
Units: metres, x east, z south, origin = north-west corner (same as the exported world)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

RIVER_STEP = 3.0
SURF_CELL = 4.0
LAKE_CELL = 4.0
NO_WATER = -1000.0
RIVER_MAX_DEPTH = 4.5     # deeper than this under a river's surface = the ground dropped away (cliff beside the river)
G = 9.81
FALL_LEAD = 5           # samples before the first falling one
FALL_DT = 0.03
FALL_REC = 2            # record every n steps
FALL_V0 = 2.6           # m/s the river runs at the brink


def _resample(points: np.ndarray, level: np.ndarray, step: float):
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    n = max(int(s[-1] / step), 1)
    t = np.linspace(0.0, s[-1], n + 1)
    x = np.interp(t, s, points[:, 0]); z = np.interp(t, s, points[:, 1]); y = np.interp(t, s, level)
    y = np.minimum.accumulate(y)                                 # never rises downstream
    return x, y, z


def river_samples(hf, r: dict) -> list[list[float]]:
    """[x, y, z, left, right, fall] every RIVER_STEP m along one of our rivers."""
    x, y, z = _resample(np.asarray(r["points"], np.float64), np.asarray(r["water_level"], np.float64), RIVER_STEP)
    out = []
    for k in range(len(x)):
        a, b = max(k - 1, 0), min(k + 1, len(x) - 1)
        dx, dz = x[b] - x[a], z[b] - z[a]
        L = math.hypot(dx, dz) or 1.0
        nx, nz = dz / L, -dx / L
        reach = []
        cap = r.get("width", 6.0) * 0.5 + 4.0       # our channels are shallow: on flat ground the scan would run out over the bank
        for sgn in (1, -1):
            d, edge = 0.0, False
            while d < cap:
                g = hf.sample(x[k] + nx * d * sgn, z[k] + nz * d * sgn)
                if g >= y[k] + 0.05:
                    break                                  # the bank
                if d > 1.0 and g < y[k] - 4.0:
                    edge = True                            # the ground drops away (a cliff/terrace edge): water can't hang in the air
                    break
                d += 0.5
            reach.append(max(d, 1.0) if edge else d + 1.5)  # a little under a bank (the terrain hides the edge), never past a drop
        out.append([round(float(x[k]), 2), round(float(y[k]), 2), round(float(z[k]), 2), round(reach[0], 2), round(reach[1], 2), 0.0])
    for k in range(1, len(out) - 1):
        drop = out[k - 1][1] - out[k + 1][1]
        out[k][5] = round(float(np.clip((drop / (2 * RIVER_STEP) - 0.25) / 0.75, 0.0, 1.0)), 3)
    return out


def _ground_normal(hf, px, pz):
    gx = hf.sample(px + 0.5, pz) - hf.sample(px - 0.5, pz)
    gz = hf.sample(px, pz + 0.5) - hf.sample(px, pz - 0.5)
    n = np.array([-gx, 1.0, -gz])
    return n / np.linalg.norm(n)


def simulate_falls(hf, rv: list, f0: int, f1: int, half_width: float = 0.0) -> dict:
    """Columns of water 0.5 m apart ride the river surface, go over where the rock drops away, fall with gravity, slide down rock with
    drag and foam, and end half a metre into the pool (port of the game's gen_tallow_hills.simulate_falls onto our heightfield)."""
    hb = hf.sample
    s0, lip, pool = max(f0 - FALL_LEAD, 0), max(f0 - 1, 0), min(f1 + 3, len(rv) - 1)
    pool_y = rv[pool][1]
    cs, cx_, cz_, cy_ = [0.0], [rv[s0][0]], [rv[s0][2]], [rv[s0][1]]
    for k in range(s0 + 1, pool + 1):
        cs.append(cs[-1] + math.hypot(rv[k][0] - rv[k - 1][0], rv[k][2] - rv[k - 1][2]))
        cx_.append(rv[k][0]); cz_.append(rv[k][2]); cy_.append(rv[k][1])
    lip_s = cs[lip - s0]

    def centre(dist):
        x_ = float(np.interp(dist, cs, cx_)); z_ = float(np.interp(dist, cs, cz_))
        e = 1.5
        tx_ = float(np.interp(dist + e, cs, cx_)) - float(np.interp(dist - e, cs, cx_))
        tz_ = float(np.interp(dist + e, cs, cz_)) - float(np.interp(dist - e, cs, cz_))
        L_ = math.hypot(tx_, tz_) or 1.0
        return x_, z_, tx_ / L_, tz_ / L_

    # width from the river above the brink (at the brink itself the sideways scan meets the drop and comes out short)
    left = min(max(rv[k][3] for k in range(s0, lip + 1)) - 1.5, 13.0)
    right = min(max(rv[k][4] for k in range(s0, lip + 1)) - 1.5, 13.0)
    left, right = max(left, half_width), max(right, half_width)     # at least the river's own width
    rng = np.random.default_rng(int(abs(rv[s0][0] * 7 + rv[s0][2] * 3)))
    cols, hits = [], []
    o = -right
    while o <= left + 1e-6:
        x0_, z0_, tx_, tz_ = centre(0.0)
        y = cy_[0]
        if abs(o) > half_width and hb(x0_ + tz_ * o, z0_ - tx_ * o) > y - 0.2:   # starts on the bank (outside the river's own width): no water
            o += 0.5
            continue
        v0 = FALL_V0 * (1.0 + rng.uniform(-0.18, 0.18))
        mode, dist, foam, pts, stuck = "flow", 0.0, 0.0, [], 0.0
        oc = o
        pos = np.array([x0_ + tz_ * o, y, z0_ - tx_ * o])
        vel = np.array([tx_ * v0, 0.0, tz_ * v0])
        side = np.array([tz_, 0.0, -tx_])
        for step in range(int(14.0 / FALL_DT)):
            if mode == "flow":
                dist += v0 * FALL_DT
                x_, z_, tx_, tz_ = centre(dist)
                side = np.array([tz_, 0.0, -tx_])
                wy = float(np.interp(min(dist, lip_s), cs, cy_))
                ahead = centre(dist + 2.0)
                gl = hb(ahead[0] + ahead[3] * (oc + 1.0), ahead[1] - ahead[2] * (oc + 1.0))
                gr = hb(ahead[0] + ahead[3] * (oc - 1.0), ahead[1] - ahead[2] * (oc - 1.0))
                here = hb(x_ + tz_ * oc, z_ - tx_ * oc)
                if here > wy - 1.2 and abs(gl - gr) > 0.05:
                    oc += (1.0 if gl < gr else -1.0) * 1.8 * FALL_DT
                pos = np.array([x_ + tz_ * oc, wy, z_ - tx_ * oc])
                vel = np.array([tx_ * v0, 0.0, tz_ * v0])
                gy = hb(pos[0], pos[2])
                if gy > pos[1] + 0.05:
                    stuck += FALL_DT
                    if stuck > 2.0:
                        break
                else:
                    stuck = 0.0
                if dist > lip_s - 6.0 and (gy < pos[1] - 2.0 or dist > lip_s + 6.0):   # over the brink where the rock drops away
                    mode = "fall"
            else:
                vel[1] -= G * FALL_DT
                vel += side * np.sign(o) * 0.1 * FALL_DT
                pos = pos + vel * FALL_DT
                gy = hb(pos[0], pos[2])
                if pos[1] < gy + 0.15:                             # on the rock: run down it
                    nrm = _ground_normal(hf, pos[0], pos[2])
                    into = float(np.dot(vel, nrm))
                    if into < 0.0:
                        vel = vel - nrm * into
                    down = np.array([0.0, -1.0, 0.0]) - nrm * (-nrm[1])
                    dl = float(np.linalg.norm(down))
                    if dl > 1e-3:
                        sp = float(np.linalg.norm(vel))
                        vel = vel + (down / dl * sp - vel) * (1.0 - math.exp(-7.0 * FALL_DT))
                    vel *= math.exp(-0.6 * FALL_DT)
                    sp = float(np.linalg.norm(vel))
                    if into < -1.0 or sp > 5.0:
                        foam = max(foam, min(1.0, 0.4 + max(-into, 0.0) / 6.0))
                        if into < -1.0 or step % 8 == 0:
                            hits.append((pos[0], gy, pos[2], min(0.2 + max(-into, 0.0) / 8.0, 1.0)))
                    pos[1] = gy + 0.15
                    if nrm[1] > 0.9 and gy < pool_y + 2.0:         # landed on flat ground almost at the pool's level (a terrace bench):
                        pts.append([round(float(pos[0]), 2), round(float(pos[1]), 2), round(float(pos[2]), 2), round(sp, 2), 1.0, 1.0])
                        hits.append((pos[0], gy, pos[2], min(sp / 20.0, 1.0)))   # it spreads out and joins the river there
                        break
                foam *= math.exp(-FALL_DT * 2.5)
                if pos[1] <= pool_y:                               # into the pool, and half a metre under it
                    pos[1] = pool_y
                    sp_ = float(np.linalg.norm(vel))
                    pts.append([round(pos[0], 2), round(pool_y + 0.05, 2), round(pos[2], 2), round(sp_, 2), 1.0, 1.0])
                    pts.append([round(pos[0] + vel[0] * 0.03, 2), round(pool_y - 0.5, 2), round(pos[2] + vel[2] * 0.03, 2), round(sp_, 2), 1.0, 1.0])
                    hits.append((pos[0], pool_y, pos[2], min(sp_ / 20.0, 1.0)))
                    break
            if step % FALL_REC == 0:
                pts.append([round(float(pos[0]), 2), round(float(pos[1]), 2), round(float(pos[2]), 2),
                            round(float(np.linalg.norm(vel)), 2), round(foam, 2), 0.0 if mode == "flow" else 1.0])
        if len(pts) > 1:
            cols.append({"o": round(o, 2), "pts": pts})
        o += 0.5
    cl: dict = {}
    for hx, hy, hz, st in hits:
        c = cl.setdefault((int(hx // 3), int(hy // 3), int(hz // 3)), [0.0, 0.0, 0.0, 0.0, 0])
        c[0] += hx; c[1] += hy; c[2] += hz; c[3] += st; c[4] += 1
    spl = sorted(cl.values(), key=lambda c: -c[3])[:5]
    splashes = [[round(c[0] / c[4], 2), round(c[1] / c[4], 2), round(c[2] / c[4], 2), round(min(c[3] / 6.0, 1.0), 2)] for c in spl]
    return {"start": s0, "end": pool, "dt": FALL_DT * FALL_REC, "pool": [rv[pool][0], pool_y, rv[pool][2], round(left + right, 2)],
            "cols": cols, "splashes": splashes}


def _point_in_poly(px, pz, poly: np.ndarray):
    inside = np.zeros(np.shape(px), bool)
    n = len(poly)
    for i in range(n):
        x1, z1 = poly[i]; x2, z2 = poly[(i + 1) % n]
        cond = ((z1 > pz) != (z2 > pz)) & (px < (x2 - x1) * (pz - z1) / ((z2 - z1) or 1e-12) + x1)
        inside ^= cond
    return inside


def lake_cells(hf, r: dict, cell: float = LAKE_CELL) -> dict:
    """Cells of `cell` m over the lake (inside its outline, grown by 2 cells) whose ground dips under its level."""
    poly = np.asarray(r["polygon"], np.float64); lvl = float(r["level"])
    x0, z0 = poly[:, 0].min() - 3 * cell, poly[:, 1].min() - 3 * cell
    ni = int((poly[:, 0].max() - x0) / cell) + 4; nj = int((poly[:, 1].max() - z0) / cell) + 4
    cells = []
    for i in range(ni):
        for j in range(nj):
            cx, cz = x0 + (i + 0.5) * cell, z0 + (j + 0.5) * cell
            lo = min(hf.sample(cx + ox, cz + oz) for ox in (-cell, 0, cell) for oz in (-cell, 0, cell))
            if lo < lvl + 0.3:
                cells.append([i, j])
    # keep only cells connected to the lake outline (not every hollow under the level in the bounding box)
    inside = {(i, j) for i, j in cells if _point_in_poly(np.array(x0 + (i + 0.5) * cell), np.array(z0 + (j + 0.5) * cell), poly)}
    keep, todo, cellset = set(inside), list(inside), {tuple(c) for c in cells}
    while todo:
        i, j = todo.pop()
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (i + d[0], j + d[1])
            if n in cellset and n not in keep:
                keep.add(n); todo.append(n)
    return {"level": round(lvl, 2), "x0": round(x0, 2), "z0": round(z0, 2), "cell": cell, "cells": sorted([list(c) for c in keep])}


def build_water(hf, log) -> tuple[dict, np.ndarray]:
    rivers, lakes = [], []
    for op in log:
        r = op.result
        if not (op.enabled and r):
            continue
        if op.kind == "river" and len(r.get("points", [])) > 2:
            rv = river_samples(hf, r)
            falls = []
            k = 1
            while k < len(rv):
                if rv[k][5] > 0.3:
                    f0 = k
                    while k < len(rv) - 1 and rv[k][5] > 0.3:
                        k += 1
                    falls.append(simulate_falls(hf, rv, f0, k, r.get("width", 6.0) * 0.5))
                k += 1
            for f in falls:                                        # from the brink to the pool the swimmer's water is the pool
                k0 = f["start"]
                while k0 < f["end"] and rv[k0][5] <= 0.3:
                    k0 += 1
                for k1 in range(k0, f["end"] + 1):
                    rv[k1][1] = min(rv[k1][1], f["pool"][1])
            rivers.append({"name": f"River_{len(rivers)}", "pts": rv, "falls": falls})
        elif op.kind == "lake" and r.get("polygon"):
            lakes.append({"name": f"Lake_{len(lakes)}", **lake_cells(hf, r)})
    return {"rivers": rivers, "lakes": lakes}, surface_grid(hf, rivers, lakes)


def surface_grid(hf, rivers: list, lakes: list) -> np.ndarray:
    sx, sz = hf.size
    sw, sh = int(math.ceil(sx / SURF_CELL)), int(math.ceil(sz / SURF_CELL))
    surface = np.full((sh, sw), NO_WATER, np.float32)
    gz, gx = np.mgrid[0:sh, 0:sw]
    wx, wz = (gx + 0.5) * SURF_CELL, (gz + 0.5) * SURF_CELL
    from scipy.ndimage import map_coordinates
    ground = map_coordinates(hf.data, [np.clip(wz / hf.cell_size, 0, hf.ny - 1), np.clip(wx / hf.cell_size, 0, hf.nx - 1)], order=1)
    for rv in rivers:
        pts = rv["pts"]
        for a, b in zip(pts, pts[1:]):
            mx, mz = (a[0] + b[0]) / 2, (a[2] + b[2]) / 2
            rad = max(a[3], a[4], b[3], b[4]) + RIVER_STEP
            i0, i1 = int(max((mx - rad) / SURF_CELL, 0)), int(min((mx + rad) / SURF_CELL + 1, sw))
            j0, j1 = int(max((mz - rad) / SURF_CELL, 0)), int(min((mz + rad) / SURF_CELL + 1, sh))
            if i1 <= i0 or j1 <= j0:
                continue
            px, pz = wx[j0:j1, i0:i1], wz[j0:j1, i0:i1]
            vx, vz = b[0] - a[0], b[2] - a[2]
            t_raw = ((px - a[0]) * vx + (pz - a[2]) * vz) / max(vx * vx + vz * vz, 1e-9)
            t = np.clip(t_raw, 0, 1)
            d = np.hypot(px - a[0] - vx * t, pz - a[2] - vz * t)
            ys = a[1] + (b[1] - a[1]) * t if abs(b[1] - a[1]) < 3.0 else np.full_like(t, min(a[1], b[1]))
            ins = (d < (max(a[3], a[4]) - 1.0)) & (t_raw > -0.2) & (t_raw < 1.2)
            ins &= ground[j0:j1, i0:i1] > ys - RIVER_MAX_DEPTH      # a cell past a terrace edge beside the river is air, not water
            cur = surface[j0:j1, i0:i1]
            surface[j0:j1, i0:i1] = np.where(ins & (ys > cur), ys, cur)
    for lk in lakes:
        for i, j in lk["cells"]:
            cx, cz = lk["x0"] + (i + 0.5) * lk["cell"], lk["z0"] + (j + 0.5) * lk["cell"]
            gi, gj = int(cx / SURF_CELL), int(cz / SURF_CELL)
            if 0 <= gi < sw and 0 <= gj < sh:
                surface[gj, gi] = max(surface[gj, gi], lk["level"])
    return surface


def write_water(out: Path, hf, log) -> dict:
    """Write WorldData/water.json + WorldData/surface.r32; returns a summary for the manifest."""
    data, surf = build_water(hf, log)
    (out / "WorldData").mkdir(parents=True, exist_ok=True)
    (out / "WorldData/water.json").write_text(json.dumps(data, separators=(",", ":")))
    surf.astype("<f4").tofile(out / "WorldData/surface.r32")
    return {"file": "WorldData/water.json", "surface": "WorldData/surface.r32", "surface_cell_m": SURF_CELL,
            "surface_size": [int(surf.shape[1]), int(surf.shape[0])], "origin": [0.0, 0.0], "no_water": NO_WATER,
            "rivers": len(data["rivers"]), "falls": sum(len(r["falls"]) for r in data["rivers"]),
            "fall_columns": sum(len(f["cols"]) for r in data["rivers"] for f in r["falls"]), "lakes": len(data["lakes"]),
            "wet_cells": int((surf > NO_WATER).sum())}
