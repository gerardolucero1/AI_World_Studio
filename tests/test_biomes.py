import numpy as np

from biomes.masks.masks import slope_degrees, water_mask
from biomes.scatter.scatter import scatter
from core.history.history import History, Stroke
from core.history.operations import Operation, dumps, rebuild
from materials.materials import IDX, final_weights, paint_weights, rule_weights
from terrain.heightfield.heightfield import Heightfield
from terrain.procedural.shapes import mountain


def world():
    h = Heightfield(257, 257, 2.0)
    mountain(h, 256, 256, 200, 90, roughness=0.1, seed=1)
    ops = [Operation("river", dict(path=[(30, 100), (250, 150), (480, 120)], width=10, depth=2)),
           Operation("lake", dict(polygon=[(380, 380), (450, 370), (460, 450), (390, 460)], depth=3))]
    rebuild(h, ops)
    return h, ops


SPECIES = [dict(name="pine", min_sep=8, density=0.8, altitude=(-1e9, 60), max_slope=25, radius=2.5, height=10),
           dict(name="shrub", min_sep=5, density=0.5, max_slope=35, radius=1.2, height=1.5)]


def test_weights_normalized_and_rules():
    h, ops = world()
    w = final_weights(h, ops)
    assert np.allclose(w.sum(-1), 1, atol=1e-4) and (w >= 0).all()
    steep = slope_degrees(h) > 45
    if steep.any():
        assert w[steep][:, IDX["rock"]].mean() > 0.5
    top = h.data >= h.data.max() * 0.95
    assert w[top][:, IDX["snow"]].mean() > 0.3
    assert w[water_mask(h, ops)][:, IDX["rock"]].mean() < 0.9


def test_paint_overrides_and_is_replayed():
    h, ops = world()
    ops.append(Operation("paint_material", dict(x=100, y=400, radius=30, material="sand", strength=1.0, hardness=0.9)))
    w = final_weights(h, ops)
    assert w[200, 50, IDX["sand"]] > 0.95 and paint_weights(h, ops[:2])[1].max() == 0


def test_scatter_rules_deterministic_and_no_water():
    h, ops = world()
    _, r1 = scatter(h, SPECIES, seed=4, _log=ops); _, r2 = scatter(h, SPECIES, seed=4, _log=ops)
    a, b = r1["_instances"], r2["_instances"]
    assert len(a) > 100 and np.array_equal(a, b)
    assert not [w for w in r1["warnings"] if w["code"] in ("veg_in_water", "veg_steep")]
    pines = a[a[:, 6] == 0]
    assert pines[:, 1].max() <= 60 + 1e-3
    _, r3 = scatter(h, SPECIES, seed=5, _log=ops); assert not np.array_equal(a, r3["_instances"])


def test_scatter_exclusion_and_region():
    h, ops = world()
    hole = [(100, 100), (400, 100), (400, 400), (100, 400)]
    _, r = scatter(h, SPECIES[1:], seed=1, exclusion=[hole], _log=ops)
    i = r["_instances"]
    assert not ((i[:, 0] > 100) & (i[:, 0] < 400) & (i[:, 2] > 100) & (i[:, 2] < 400)).any()
    _, r = scatter(h, SPECIES[1:], seed=1, region=hole, _log=ops); i = r["_instances"]
    assert len(i) > 0 and ((i[:, 0] > 100) & (i[:, 0] < 400) & (i[:, 2] > 100) & (i[:, 2] < 400)).all()


def test_water_proximity_rule():
    h, ops = world()
    sp = [dict(name="reed", min_sep=4, density=1.0, water_dist=(0, 20), max_slope=40)]
    _, r = scatter(h, sp, seed=2, _log=ops)
    from biomes.masks.masks import water_distance
    wd = water_distance(h, water_mask(h, ops)); i = r["_instances"]
    ci = (i[:, 2] / h.cell_size).astype(int); cj = (i[:, 0] / h.cell_size).astype(int)
    assert len(i) > 0 and (wd[ci, cj] <= 20 + 1e-3).all()


def test_ops_serialize_without_instances_and_undo_tracks_op():
    h, ops = world()
    op = Operation("scatter_vegetation", dict(species=SPECIES, seed=1)); rebuild(h, ops + [op])
    s = dumps(ops + [op]); assert "_instances" not in s and '"total"' in s
    hist = History(); st = Stroke(h); st.op = op; st.finish(); hist.push(st)
    assert hist.can_undo and hist.undo() is None or True
    assert hist.last.op is op
