import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.app import app
from core.world import service as S
from core.world.session import WorldSession


@pytest.fixture(autouse=True)
def fresh():
    S.SESSION = WorldSession()          # tools read the module-level SESSION
    yield


def build_region():
    assert S.create_world("t", 512, 257)["ok"]
    assert S.apply_terrain_noise(seed=1, amplitude=10, feature_size_m=200)["ok"]
    assert S.create_mountain(256, 256, 150, 60, seed=2)["ok"]
    r = S.create_river([[20, 100], [200, 160], [490, 130]], width=8, depth=2)
    assert r["ok"]
    assert S.create_lake([[380, 380], [450, 370], [460, 440], [390, 450]], depth=3)["ok"]
    assert S.scatter_vegetation(["conifer_forest", "riparian_reeds"], seed=3)["ok"]
    return r


def test_agent_flow_and_inspection(tmp_path):
    build_region()
    info = S.get_world_info(); assert info["operations"] == 5 and info["size_m"][0] == 512
    v = S.validate_world(); assert v["counts"]["error"] == 0
    reg = S.inspect_region(380, 370, 90, 90); assert reg["ok"] and reg["water_fraction"] > 0.1
    assert S.inspect_terrain()["zones"] and S.analyze_slope()["ok"] and S.analyze_elevation()["ok"]
    top = S.get_world_preview(256); assert top["path"].endswith(".png")
    view = S.capture_world_view("player", 100, 100, yaw=0, width=160, height=100); assert view["ok"]
    from PIL import Image
    assert Image.open(view["path"]).size == (160, 100) and Image.open(top["path"]).size == (256, 256)


def test_checkpoint_rollback_compare_undo():
    build_region(); S.create_checkpoint("good")
    before = S.SESSION.hf.data.copy()
    S.create_mountain(100, 100, 80, 200)
    cmp = S.compare_world_versions("good"); assert cmp["max_rise_m"] > 100 and cmp["new_operations"] == ["mountain"]
    S.rollback_to_checkpoint("good"); assert np.array_equal(S.SESSION.hf.data, before)
    assert S.undo()["ok"]
    assert S.rollback_to_checkpoint("nope")["ok"] is False


def test_auto_checkpoint_before_large_ops_and_task_flow():
    S.create_world("t", 256, 129)
    r = S.apply_erosion("thermal", 5, background=True); assert r["ok"] and r["task_id"]
    st = S.TASKS.wait(r["task_id"], 30); assert st["status"] == "completed"
    assert any(k.startswith("auto-") for k in S.SESSION.checkpoints)
    assert S.get_task_status("task-999")["ok"] is False
    assert S.apply_erosion("bogus")["ok"] is False


def test_errors_are_structured_not_raised():
    S.create_world("t", 256, 129)
    assert S.paint_terrain_material(10, 10, 5, "lava")["ok"] is False
    assert S.scatter_vegetation(["nope"])["ok"] is False
    assert S.create_world("x", 10, 5)["ok"] is False
    assert S.sculpt_terrain("explode", [[10, 10]])["ok"] is False
    assert not S.SESSION.op_log                              # failed ops are not recorded
    assert S.SESSION.hf.data.max() == 0


def test_save_load_regenerates_runtime_data(tmp_path):
    build_region(); S.create_cliff([[100, 300], [200, 290], [210, 400], [110, 410]], 50)
    before = S.SESSION.hf.data.copy(); n = S.list_operations()["operations"]
    p = tmp_path / "w.awp"; assert S.save_world(str(p))["ok"]
    S.create_world("other", 256, 129)
    assert S.load_world(str(p))["ok"]
    assert np.array_equal(S.SESSION.hf.data, before) and len(S.list_operations()["operations"]) == len(n)
    kinds = {o.kind: o.result for o in S.SESSION.op_log}
    assert "_mesh" in kinds["cliff_plateau"] and "_instances" in kinds["scatter_vegetation"]
    assert S.validate_world()["counts"]["error"] == 0 and S.get_world_preview(128)["ok"]


def test_rest_api():
    c = TestClient(app)
    assert any(t["name"] == "create_mountain" for t in c.get("/tools").json())
    assert c.post("/tools/create_world", json={"size_m": 256, "resolution": 129}).json()["ok"]
    r = c.post("/tools/create_mountain", json={"x": 128, "y": 128, "radius": 80, "height": 40}).json()
    assert r["ok"] and r["result"] == {}
    assert c.post("/tools/create_mountain", json={"x": 1}).status_code == 422
    assert c.post("/tools/nope", json={}).status_code == 404
