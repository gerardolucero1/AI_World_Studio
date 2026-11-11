"""Editor analysis layers and the fast water mask behind them."""
import time

import numpy as np
import pytest

from core.world import service as S
from core.world.session import WorldSession
from render.overlays import MODES, overlay_rgba


@pytest.fixture(scope="module")
def world():
    S.SESSION = WorldSession(); assert S.generate_landscape("highland_lake", 768, 2)["ok"]
    return S.SESSION


def test_every_overlay_is_an_rgba_image_of_the_heightfield(world):
    hf, log = world.hf, world.op_log
    assert overlay_rgba(hf, log, "none") is None
    for m in MODES:
        if m == "none":
            continue
        t = time.time(); img = overlay_rgba(hf, log, m)
        assert img.shape == (hf.ny, hf.nx, 4) and img.dtype == np.uint8 and img[..., 3].max() > 0
        assert time.time() - t < 5, f"{m} too slow for the editor"
    with pytest.raises(ValueError):
        overlay_rgba(hf, log, "nope")


def test_overlays_mean_what_the_legend_says(world):
    from biomes.masks.masks import slope_degrees, water_mask
    hf, log = world.hf, world.op_log
    s = slope_degrees(hf); img = overlay_rgba(hf, log, "slope")
    assert img[s < 10][:, 1].mean() > img[s < 10][:, 0].mean()          # gentle ground is green
    assert img[s > 50][:, 0].mean() > img[s > 50][:, 1].mean()          # cliffs are red
    wm = water_mask(hf, log); w = overlay_rgba(hf, log, "water")
    assert wm.any() and (w[wm][:, 2] > 200).all()
    walk = overlay_rgba(hf, log, "walkable")
    assert (walk[..., 1] > walk[..., 0]).any() and (walk[..., 0] > walk[..., 1]).any()   # some reachable, some not


def test_lake_mask_matches_the_exact_polygon_test(world):
    from biomes.masks.masks import _grid_xy, poly_inside
    from features.curves.curve import sample_curve
    from terrain.procedural.shapes import _poly_sdf
    hf = world.hf
    lake = next(o.result for o in world.op_log if o.kind == "lake")
    poly = lake["polygon"]; outline = sample_curve(poly + [poly[0]], hf.cell_size * 2).astype(np.float32)
    X, Y = _grid_xy(hf)
    sdf = _poly_sdf(X, Y, outline)
    fast = poly_inside(hf, outline)
    diff = (sdf <= 0) ^ fast
    assert np.abs(sdf[diff]).max() <= hf.cell_size * 1.42                # only cells on the outline (within one cell) may differ
    assert diff.sum() < 0.05 * (sdf <= 0).sum()
