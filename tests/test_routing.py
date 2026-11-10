import numpy as np

from core.history.operations import Operation, rebuild
from features.paths.routing import route, traversability
from terrain.heightfield.heightfield import Heightfield
from terrain.procedural.shapes import mountain


def hill():
    h = Heightfield(257, 257, 2.0)
    mountain(h, 256, 256, 200, 90, roughness=0, profile=1.0)
    return h


def test_route_prefers_gentle_grade_and_reaches_target():
    h = hill()
    r = route(h, [], (40, 256), (256, 256))
    assert r["ok"] and r["points"][0] == [40.0, 256.0] and np.hypot(*(np.array(r["points"][-1]) - [256, 256])) < 6
    straight = np.array([[40 + i * 216 / 100, 256] for i in range(101)])
    hs = np.array([h.sample(x, y) for x, y in straight]); g_straight = np.abs(np.diff(hs)).max() / (216 / 100)
    assert r["max_grade"] <= g_straight + 1e-6                      # never worse than the straight line


def test_route_avoids_water_and_reports_unreachable():
    h = Heightfield(129, 129, 2.0)
    ops = [Operation("lake", dict(polygon=[(100, 60), (160, 60), (160, 200), (100, 200)], depth=3))]
    rebuild(h, ops)
    r = route(h, ops, (20, 128), (240, 128))
    assert r["ok"]
    from biomes.masks.masks import water_mask
    wm = water_mask(h, ops)
    pts = np.array(r["points"]); assert not any(wm[int(y / 2), int(x / 2)] for x, y in pts)      # simplified polyline stays dry
    assert route(h, ops, (130, 128), (240, 128))["ok"] is False                                   # start in water


def test_max_grade_blocks_cliffs():
    h = Heightfield(129, 129, 2.0)
    h.data[:, 64:] = 40                                              # sheer step in the middle
    assert route(h, [], (20, 128), (230, 128), max_grade=0.5)["ok"] is False
    assert route(h, [], (20, 128), (230, 128))["ok"]                 # allowed at high cost when no limit (ramp will be carved)


def test_traversability_flags_unreachable_plateau():
    h = Heightfield(129, 129, 2.0)
    h.data[40:90, 40:90] = 60                                        # isolated raised plateau with sheer walls
    t = traversability(h, [], start=(10, 10), max_slope_deg=40)
    assert t["ok"] and t["summit"]["height"] == 60 and not t["summit"]["reachable"]
    assert t["unreachable_regions"] and 0 < t["reachable_fraction"] < 1
    ramp = np.linspace(0, 60, 40)
    h.data[60:70, 20:60] = ramp[None, :]                             # gentle ramp up to the plateau
    t2 = traversability(h, [], start=(10, 10), max_slope_deg=60)
    assert t2["summit"]["reachable"]


def test_route_has_no_out_and_back_spurs():
    h = hill()
    # a waypoint far off the natural line used to force the route to go there and come back over the same cells
    r = route(h, [], (40, 256), (470, 256), waypoints=[(256, 470)])
    pts = np.array(r["points"]); assert r["ok"]
    seg = np.diff(pts, axis=0); seg /= np.maximum(np.linalg.norm(seg, axis=1, keepdims=True), 1e-9)
    cos = (seg[1:] * seg[:-1]).sum(1)
    assert cos.min() > -0.5                                           # no hairpin reversals (> 120 deg turn) in the simplified route
    nodes = {(round(x / 4), round(y / 4)) for x, y in pts}
    assert len(nodes) == len(pts)                                     # never visits the same area twice
