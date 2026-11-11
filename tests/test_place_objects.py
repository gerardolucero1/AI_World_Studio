"""Hand-placed objects (landmark tree, boulder) and vegetation that follows terrain edits."""
import json

from core.world import service as S
from core.world.session import WorldSession
from exporters.godot.exporter import ExportSettings, export_world


def fresh():
    S.SESSION = WorldSession(); S.create_world("p", 256, 129)
    return S.SESSION


def test_place_objects_validates_and_grounds():
    fresh()
    r = S.place_objects([{"kind": "oak", "x": 100, "y": 120, "scale": 3, "yaw": 30}, {"kind": "rock", "x": 60, "y": 60, "scale": 2, "sink": 0.4}])
    assert r["ok"] and r["result"]["counts"] == {"oak": 1, "rock": 1}
    inst = S.SESSION.op_log[-1].result["_instances"]
    assert inst[0][4] == 4.6 * 3 and abs(inst[0][5] - 9.5 * 3) < 1e-4 and inst[0][3] == 30
    assert abs(inst[1][1] - (S.SESSION.hf.sample(60, 60) - 0.4)) < 1e-4
    for bad in ([], [{"kind": "castle", "x": 1, "y": 1}], [{"kind": "oak", "x": 999, "y": 1}], [{"kind": "oak", "x": 1, "y": 1, "scale": 50}]):
        assert not S.place_objects(bad)["ok"]


def test_vegetation_and_objects_follow_terrain_edits_and_undo():
    s = fresh()
    S.place_objects([{"kind": "oak", "x": 100, "y": 120}]); S.scatter_vegetation(["bushes"], seed=1)
    S.create_mountain(110, 120, 60, 30)
    tree = next(o for o in s.op_log if o.kind == "place_objects").result["_instances"]
    bushes = next(o for o in s.op_log if o.kind == "scatter_vegetation").result["_instances"]
    assert tree[0][1] > 10 and abs(tree[0][1] - s.hf.sample(100, 120)) < 1e-3
    assert max(abs(b[1] - s.hf.sample(b[0], b[2])) for b in bushes) < 1e-3
    S.undo()
    assert abs(tree[0][1]) < 1e-3


def test_placed_objects_are_exported_and_survive_save_load(tmp_path):
    fresh()
    S.place_objects([{"kind": "oak", "x": 100, "y": 120, "scale": 3}, {"kind": "pine", "x": 40, "y": 40}])
    S.save_world(str(tmp_path / "w.awp")); S.SESSION = WorldSession(); assert S.load_world(str(tmp_path / "w.awp"))["ok"]
    op = next(o for o in S.SESSION.op_log if o.kind == "place_objects")
    assert len(op.result["_instances"]) == 2
    export_world(S.SESSION, tmp_path / "pkg", ExportSettings(navigation="none", grass=False))
    mf = json.loads((tmp_path / "pkg/WorldData/manifest.json").read_text())
    assert mf["vegetation"]["species"] == {"oak": 1, "pine": 1}
