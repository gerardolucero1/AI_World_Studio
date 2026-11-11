"""Analysis layers drawn over the terrain in the editor: height, slope (walk / climb), water, and what a walker can reach.
Returns RGBA8 images (rows = y/south, columns = x/east) at heightfield resolution; alpha = how strongly to tint the terrain."""
from __future__ import annotations

import numpy as np

from biomes.masks.masks import slope_degrees, water_mask

MODES = {
    "none": ("Ninguna", ""),
    "height": ("Altura", "azul = bajo · verde · amarillo · marrón · blanco = alto · líneas cada 10 m"),
    "slope": ("Pendiente", "verde < 20° se camina · amarillo 20–35° cuesta · naranja 35–45° límite · rojo > 45° hay que trepar"),
    "water": ("Agua", "azul = agua (lagos y ríos) · celeste = orilla a menos de 8 m"),
    "walkable": ("Transitable a pie", "verde = se llega caminando desde el inicio · rojo = tierra a la que no se llega · gris = agua"),
}


def _ramp(t: np.ndarray, stops: list[tuple[float, tuple[float, float, float]]]) -> np.ndarray:
    xs = np.array([s for s, _ in stops]); cs = np.array([c for _, c in stops])
    return np.stack([np.interp(t, xs, cs[:, k]) for k in range(3)], -1)


def overlay_rgba(hf, log, mode: str, walk_slope_deg: float = 40.0) -> np.ndarray | None:
    if mode == "none":
        return None
    h = hf.data
    out = np.zeros(h.shape + (4,), np.float32)
    if mode == "height":
        lo, hi = float(h.min()), float(h.max())
        t = (h - lo) / max(hi - lo, 1e-6)
        out[..., :3] = _ramp(t, [(0, (0.15, 0.35, 0.85)), (0.25, (0.25, 0.7, 0.3)), (0.5, (0.95, 0.85, 0.3)),
                                 (0.75, (0.6, 0.4, 0.25)), (1.0, (1.0, 1.0, 1.0))])
        out[..., 3] = 0.75
        contour = np.abs(((h / 10.0) + 0.5) % 1.0 - 0.5) * 10.0 < 0.35        # thin line every 10 m
        out[contour, :3] *= 0.45
    elif mode == "slope":
        s = slope_degrees(hf)
        out[..., :3] = _ramp(s, [(0, (0.2, 0.75, 0.3)), (20, (0.2, 0.75, 0.3)), (20.01, (0.95, 0.85, 0.2)), (35, (0.95, 0.85, 0.2)),
                                 (35.01, (0.95, 0.5, 0.15)), (45, (0.95, 0.5, 0.15)), (45.01, (0.85, 0.15, 0.15)), (90, (0.6, 0.05, 0.1))])
        out[..., 3] = 0.7
    elif mode == "water":
        from scipy.ndimage import distance_transform_edt
        wm = water_mask(hf, log)
        near = (distance_transform_edt(~wm) * hf.cell_size < 8.0) & ~wm
        out[wm, :] = (0.1, 0.35, 0.95, 0.85)
        out[near, :] = (0.5, 0.85, 1.0, 0.6)
    elif mode == "walkable":
        from features.paths.routing import traversability
        t = traversability(hf, log, max_slope_deg=walk_slope_deg)
        st = t["_stride"]
        reach = np.kron(t["_mask"], np.ones((st, st), bool))[: h.shape[0], : h.shape[1]]
        pad = np.zeros(h.shape, bool); pad[: reach.shape[0], : reach.shape[1]] = reach
        wm = water_mask(hf, log)
        out[..., :] = (0.85, 0.2, 0.2, 0.55)
        out[pad, :] = (0.2, 0.8, 0.3, 0.45)
        out[wm, :] = (0.5, 0.5, 0.55, 0.6)
    else:
        raise ValueError(f"unknown overlay {mode!r}; choose from {list(MODES)}")
    return np.ascontiguousarray((np.clip(out, 0, 1) * 255).astype(np.uint8))
