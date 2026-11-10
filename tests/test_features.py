import numpy as np

from core.history.operations import Operation, dumps, run
from features.curves.curve import sample_curve
from features.lakes.lake import create_lake
from features.paths.path import create_path
from features.rivers.river import create_river
from terrain.heightfield.heightfield import Heightfield
from terrain.procedural.shapes import mountain


def slope_terrain(n=257, drop=60.0):
    h = Heightfield(n, n, 2.0)
    h.data[:] = np.linspace(drop, 0, n, dtype=np.float32)[None, :]   # descends along +x
    return h


def test_curve_spacing_and_endpoints():
    p = sample_curve([(0, 0), (100, 50), (200, 0)], 5)
    assert np.allclose(p[0], (0, 0)) and np.allclose(p[-1], (200, 0))
    seg = np.hypot(*np.diff(p, axis=0).T); assert seg.std() < 0.5


def test_river_carves_and_flows_downhill():
    h = slope_terrain(); before = h.data.copy()
    rect, rep = create_river(h, [(20, 256), (250, 200), (480, 256)], width=12, depth=3)
    bed = np.array(rep["bed"]); assert (np.diff(bed) <= 1e-6).all()
    assert h.data.min() < before.min() or (h.data < before - 1).any()
    assert (h.data <= before + 1e-5).all() and not rep["warnings"]
    w = np.array(rep["water_level"]); assert (np.diff(w) <= 1e-6).all()


def test_river_uphill_warns():
    h = slope_terrain()
    _, rep = create_river(h, [(480, 256), (20, 256)], width=8, depth=2)  # against the slope
    assert any(w["code"] == "river_uphill" for w in rep["warnings"])


def test_lake_level_below_rim_no_leak():
    h = Heightfield(257, 257, 2.0); h.data[:] = 10
    _, rep = create_lake(h, [(200, 200), (320, 190), (340, 320), (210, 330)], depth=4)
    assert rep["level"] < 10 and not [w for w in rep["warnings"] if w["code"] == "lake_leak"]
    assert h.sample(270, 260) < rep["level"] - 1


def test_lake_leak_detected_with_high_level():
    h = Heightfield(257, 257, 2.0); h.data[:] = 10
    _, rep = create_lake(h, [(200, 200), (320, 190), (340, 320), (210, 330)], level=12)
    assert any(w["code"] == "lake_leak" for w in rep["warnings"])


def test_path_grade_limited_and_warns_when_steep():
    h = Heightfield(257, 257, 2.0)
    mountain(h, 256, 256, 200, 120, roughness=0)
    _, rep = create_path(h, [(60, 256), (256, 256), (450, 256)], max_slope=0.15)
    assert rep["max_grade"] <= 0.15 + 1e-3
    assert any(w["code"] == "path_too_steep" for w in rep["warnings"])


def test_path_on_flat_integrates():
    h = slope_terrain(drop=10)
    _, rep = create_path(h, [(40, 100), (450, 120)], width=4, max_slope=0.25)
    assert not rep["warnings"]
    assert abs(h.sample(200, 108) - np.interp(np.hypot(160, 8), rep["points"] and np.cumsum([0] + list(np.hypot(*np.diff(np.array(rep["points"]), axis=0).T))), rep["elevation"])) < 3


def test_features_via_operations_deterministic_and_serializable():
    ops = [Operation("river", dict(path=[(20, 256), (250, 200), (480, 256)], width=10, depth=2)),
           Operation("path", dict(path=[(40, 100), (450, 120)]))]
    def build():
        h = slope_terrain(); [run(h, o) for o in ops]; return h.data.copy()
    a = build(); assert np.array_equal(a, build())
    assert ops[0].result["length"] > 400 and "river" in dumps(ops)


def stepped_terrain(n=257):
    """Three benches separated by 12 m cliffs, descending along +x."""
    h = Heightfield(n, n, 2.0)
    x = np.arange(n) * 2.0
    h.data[:] = np.where(x < 150, 36.0, np.where(x < 300, 24.0, 12.0)).astype(np.float32)[None, :]
    from scipy.ndimage import gaussian_filter1d
    h.data[:] = gaussian_filter1d(h.data, 1.2, axis=1)               # steep but not vertical risers
    return h


def test_river_over_cliffs_makes_waterfalls_with_pools():
    h = stepped_terrain()
    before = h.data.copy()
    _, rep = create_river(h, [(20, 256), (480, 256)], width=6, depth=2, bank_width=6)
    assert len(rep["falls"]) == 2 and all(f["height"] > 8 for f in rep["falls"])
    for f in rep["falls"]:
        assert f["top"][1] > f["bottom"][1] and f["lip_index"] < f["base_index"]
        bx, bz = f["bottom"][0], f["bottom"][2]
        assert h.sample(bx, bz) < before[int(bz / 2), int(bx / 2)] - 1.0                 # plunge pool carved at the foot
    w = np.array(rep["water_level"]); assert (np.diff(w) <= 1e-6).all()                  # still monotone
    # the cliff face itself is NOT carved away: the lip stays high
    lip = rep["falls"][0]["lip_index"]; lx, lz = rep["points"][lip]
    assert h.sample(lx + 3, lz + 20) > 20 or h.sample(lx - 4, lz) > 30


def test_river_without_cliffs_has_no_falls():
    h = slope_terrain(); _, rep = create_river(h, [(20, 256), (480, 256)], width=8, depth=2)
    assert rep["falls"] == [] and rep["fall_segments"] == []


def test_route_river_descends_and_prefers_the_low_riser():
    from features.paths.routing import route_river
    h = stepped_terrain()
    # a gentle ramp through the first riser (z 120-200), away from the straight line between the endpoints
    h.data[60:100, 50:95] = np.linspace(36, 24, 45, dtype=np.float32)[None, :]
    r = route_river(h, (20, 300), (480, 300))
    assert r["ok"] and r["drop"] > 20
    pts = np.array(r["points"]); at_riser = pts[np.argmin(np.abs(pts[:, 0] - 150))]
    assert 110 < at_riser[1] < 210                                                        # detoured into the notch (z 120-200) instead of crossing the full cliff
    assert r["max_step_drop"] < 12                                                        # and never fell the whole 12 m in one step
    up = route_river(h, (480, 300), (20, 300))
    assert up["ok"] is False or up["drop"] < 5                                           # cannot flow uphill (at most small tolerated climbs)


def test_river_auto_tool_and_waterfall_export(tmp_path):
    from core.world import service as S
    from core.world.session import WorldSession
    S.SESSION = WorldSession(); S.create_world("t", 512, 257)
    S.SESSION.hf.data[:] = stepped_terrain().data
    r = S.create_river_auto([20, 256], [480, 256], width=6, depth=2)
    assert r["ok"] and len(r["falls"]) >= 1
    from exporters.godot.exporter import export_world
    e = export_world(S.SESSION, tmp_path / "pkg")
    assert e["validation"]["ok"] and e["features"]["waterfalls"] == len(r["falls"])
    assert list((tmp_path / "pkg/Features/Rivers").glob("River_0_Fall_*.glb")) and (tmp_path / "pkg/Materials/waterfall.tres").exists()
    assert 'name="Fall_0"' in (tmp_path / "pkg/World.tscn").read_text()
