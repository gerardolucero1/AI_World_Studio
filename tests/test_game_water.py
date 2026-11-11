"""Water as data for a game's own water system (water.json + surface.r32, format of Wicktale's water.gd)."""
import json

import numpy as np

from core.world.session import WorldSession
from exporters.godot.exporter import ExportSettings, export_world
from exporters.godot.game_water import NO_WATER, SURF_CELL, build_water


def world_with_falls():
    from core.world import service as S
    S.SESSION = WorldSession()
    r = S.generate_landscape("highland_lake", 768, 3)
    assert r["ok"]
    return S.SESSION


def test_water_data_matches_the_game_format_and_the_terrain():
    sess = world_with_falls(); hf = sess.hf
    data, surf = build_water(hf, sess.op_log)
    assert data["rivers"] and data["lakes"]
    rv = data["rivers"][0]
    pts = np.array(rv["pts"], np.float64)
    assert pts.shape[1] == 6 and np.all(np.diff(pts[:, 1]) <= 1e-6)                  # [x, y, z, left, right, fall]; never rises downstream
    assert np.all(pts[:, 3] >= 1.0) and np.all(pts[:, 4] >= 1.0) and pts[:, 5].max() > 0.3
    assert rv["falls"], "the preset's river drops over terraces"
    for f in rv["falls"]:
        assert set(f) >= {"start", "end", "dt", "pool", "cols", "splashes"} and len(f["pool"]) == 4
        assert len(f["cols"]) >= 4                                                    # at least the river's own width
        in_pool = 0
        for c in f["cols"]:
            p = np.array(c["pts"])
            assert p.shape[1] == 6 and p[-1][5] == 1.0                              # ends falling (in the air, on the rock or in the pool)
            assert p[0][1] - p[-1][1] > 3.0                                         # it really falls
            in_pool += p[-1][1] < f["pool"][1] + 2.3 and p[-1][4] == 1.0              # into the pool, or onto flat ground at its level, foaming
        assert in_pool >= 0.8 * len(f["cols"])                                       # (a column may still stall on the rock, as in the game)
    lk = data["lakes"][0]
    assert set(lk) >= {"name", "level", "x0", "z0", "cell", "cells"} and len(lk["cells"]) > 50
    assert surf.shape == (int(np.ceil(hf.size[1] / SURF_CELL)), int(np.ceil(hf.size[0] / SURF_CELL)))
    i, j = lk["cells"][len(lk["cells"]) // 2]
    cx, cz = lk["x0"] + (i + 0.5) * lk["cell"], lk["z0"] + (j + 0.5) * lk["cell"]
    assert abs(surf[int(cz / SURF_CELL), int(cx / SURF_CELL)] - lk["level"]) < 1e-3     # surface_at on the lake = its level
    assert hf.sample(cx, cz) < lk["level"]
    wet = surf > NO_WATER
    assert 0 < wet.mean() < 0.3
    # no water hangs in the air: where the grid is wet, the ground is never far below the surface outside the lake/pools
    gz, gx = np.nonzero(wet)
    depth = np.array([surf[a, b] - hf.sample((b + 0.5) * SURF_CELL, (a + 0.5) * SURF_CELL) for a, b in zip(gz, gx)])
    assert np.percentile(depth, 98) < lk["level"] - float(hf.data.min()) + 2.0


def test_export_with_a_game_water_script_replaces_our_water_meshes(tmp_path):
    sess = world_with_falls()
    export_world(sess, tmp_path / "a", ExportSettings(navigation="none", grass=False))
    export_world(sess, tmp_path / "b", ExportSettings(navigation="none", grass=False, water_script="res://scripts/world/water.gd"))
    for d in ("a", "b"):
        assert (tmp_path / d / "WorldData/water.json").exists() and (tmp_path / d / "WorldData/surface.r32").exists()
    a, b = (tmp_path / "a/World.tscn").read_text(), (tmp_path / "b/World.tscn").read_text()
    assert "Lake_0" in a and "mat_waterfall" in a and 'id="water_gd"' not in a
    assert 'path="res://scripts/world/water.gd" id="water_gd"' in b and 'water_file = "res://WorldData/water.json"' in b
    assert "Lake_0.glb" not in b and "_Fall_" not in b
    mf = json.loads((tmp_path / "b/WorldData/manifest.json").read_text())
    w = mf["water"]
    assert w["drawn_by"] == "res://scripts/world/water.gd" and w["falls"] >= 1 and w["lakes"] == 1
    assert f"surf_size = Vector2i({w['surface_size'][0]}, {w['surface_size'][1]})" in b
    raw = np.fromfile(tmp_path / "b/WorldData/surface.r32", "<f4")
    assert raw.size == w["surface_size"][0] * w["surface_size"][1] and (raw > NO_WATER).sum() == w["wet_cells"]
