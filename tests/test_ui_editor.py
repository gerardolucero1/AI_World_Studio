"""Desktop editor (PySide6, offscreen): shares the world with the service tools, and the export dialog reads the target Godot project."""
import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture
def window(qapp):
    from app.main import MainWindow
    w = MainWindow()
    yield w
    w.close()


def fake_game(root):
    """A Godot project with Terrain3D, its own Terrain3D material inside a level scene, and a water script."""
    (root / "addons/terrain_3d").mkdir(parents=True)
    (root / "addons/terrain_3d/plugin.cfg").write_text('[plugin]\nname="Terrain3D"\nversion="1.1.0"\n')
    (root / "addons/terrain_3d/terrain.gdextension").write_text("[configuration]\n")
    (root / "project.godot").write_text('config_version=5\n[application]\nconfig/name="My Game"\n')
    (root / "levels").mkdir(); (root / "scripts").mkdir()
    (root / "levels/forest.tscn").write_text('[gd_scene format=3]\n\n[ext_resource type="Shader" path="res://shaders/toon.gdshader" id="1_s"]\n\n'
                                             '[sub_resource type="Terrain3DMaterial" id="M"]\nshader_override_enabled = true\nshader_override = ExtResource("1_s")\n\n'
                                             '[node name="Forest" type="Node3D"]\n')
    (root / "levels/plain.tscn").write_text('[gd_scene format=3]\n\n[sub_resource type="Terrain3DMaterial" id="M"]\n\n[node name="P" type="Node3D"]\n')
    (root / "scripts/water.gd").write_text('extends Node3D\n@export var water_file := ""\nfunc surface_at(x: float, z: float) -> float:\n\treturn 0.0\n')
    (root / "scripts/other.gd").write_text("extends Node\nfunc surface_at_not(x):\n\tpass\n")


def test_inspect_godot_project_tool(tmp_path):
    from core.world import service as S
    fake_game(tmp_path)
    r = S.inspect_godot_project(str(tmp_path))
    assert r["ok"] and r["project_name"] == "My Game" and r["terrain3d"] == {"installed": True, "version": "1.1.0"}
    assert [m["path"] for m in r["terrain3d_materials"]] == ["res://levels/forest.tscn", "res://levels/plain.tscn"]   # custom look first
    assert r["terrain3d_materials"][0]["shader_override"] == "res://shaders/toon.gdshader"
    assert r["water_scripts"] == ["res://scripts/water.gd"]
    assert r["suggestion"] == {"terrain": "terrain3d", "terrain3d_material": "res://levels/forest.tscn", "water_script": "res://scripts/water.gd"}
    assert not S.inspect_godot_project(str(tmp_path / "nope"))["ok"]


def test_editor_and_tools_share_one_world(window):
    """'Generar paisaje' and any service tool act on the world the editor shows (they used to go to a hidden second session)."""
    from core.world import presets
    from core.world import service as S
    assert S.SESSION is window.session
    assert presets.generate_landscape("highland_lake", 768, 2)["ok"]
    assert len(window.session.op_log) > 5 and window.session.hf.size[0] == 768
    S.create_mountain(300, 300, 60, 20)
    assert window.session.op_log[-1].kind == "mountain"


def test_export_dialog_prefills_from_the_game_project(window, tmp_path):
    from app.export_dialog import ExportDialog
    fake_game(tmp_path)
    d = ExportDialog(window)
    d.r_host.setChecked(True); d.e_project.setText(str(tmp_path)); d.e_sub.setText("generated_worlds/w1"); d._detect()
    assert "Terrain3D 1.1.0" in d.lbl_detect.text() and "water.gd" in d.lbl_detect.text()
    assert d.c_terrain.currentData() == "terrain3d" and d.c_mat.currentText() == "res://levels/forest.tscn"
    assert d.c_water.currentText() == "res://scripts/water.gd"
    d.s_path_id.setValue(1)
    k = d.kwargs()
    assert k["godot_project"] == str(tmp_path) and k["subfolder"] == "generated_worlds/w1" and k["terrain"] == "terrain3d"
    assert k["terrain3d_material"] == "res://levels/forest.tscn" and k["water_script"] == "res://scripts/water.gd"
    assert k["terrain3d_texture_ids"]["dirt"] == 1 and k["terrain3d_texture_ids"]["rock"] == 0
    # standalone: no game paths, mesh terrain
    d.r_alone.setChecked(True); d.e_out.setText(str(tmp_path / "out")); d.c_terrain.setCurrentIndex(d.c_terrain.findData("mesh")); d.c_water.setCurrentText("")
    k = d.kwargs()
    assert k["path"] == str(tmp_path / "out") and "godot_project" not in k and "terrain3d_material" not in k and "water_script" not in k
    d.close()


def test_export_from_the_editor_runs_in_the_background(window, qapp, tmp_path):
    from app.export_dialog import run_export
    from core.world import service as S
    S.create_world("ui", 256, 129); S.create_mountain(128, 128, 60, 25)
    S.create_lake([[30, 180], [80, 180], [80, 230], [30, 230]], depth=2)
    got = {}
    run_export(window, dict(path=str(tmp_path / "pkg"), navigation="none", grass=False), got.update)
    t0 = time.time()
    while not got and time.time() - t0 < 120:
        qapp.processEvents(); time.sleep(0.05)
    assert got.get("ok") and got["validation"]["ok"], got
    mf = json.loads((tmp_path / "pkg/WorldData/manifest.json").read_text())
    assert mf["world"]["size_m"] == [256.0, 256.0] and mf["water"]["lakes"] == 1


def test_place_tool_in_the_editor(window):
    from core.world import service as S
    S.create_world("ui", 256, 129)
    window.select_tool("place")
    assert window.g_place.isVisible() or not window.isVisible()        # offscreen windows are never "visible"
    window.c_kind.setCurrentIndex(window.c_kind.findData("oak")); window.s_scale.setValue(3.0)
    assert abs(window.view.marker_radius - 4.6 * 3) < 1e-6              # the ring shows the tree's real size
    window.ch_yaw_rand.setChecked(False); window.s_yaw.setValue(45)
    window.on_placed("place", [(120.0, 80.0)])
    op = window.session.op_log[-1]
    assert op.kind == "place_objects" and op.params["objects"][0] == dict(kind="oak", x=120.0, y=80.0, scale=3.0, yaw=45.0, sink=0.0)
    assert window.hist.item(window.hist.count() - 1).text().endswith("Objetos colocados")


def test_brush_strokes_are_logged_and_survive_editing_a_past_operation(window):
    import numpy as np
    from core.world import service as S
    S.create_world("ui", 256, 129)
    S.create_mountain(60, 60, 40, 20)
    v = window.view
    window.select_tool("raise"); v.params.radius = 15; v.params.strength = 1.0
    v._cursor_pick = (180.0, 0.0, 180.0); v._begin_stroke(); v._dab(); v._end_stroke()
    op = window.session.op_log[-1]
    assert op.kind == "brush" and len(op.params["dabs"]) >= 2 and op.params["dabs"][0][0] == "raise"
    bump = window.session.hf.sample(180, 180)
    assert bump > 1
    assert S.modify_operation(0, {"height": 35})["ok"]                       # edit the mountain (before the stroke)
    assert abs(window.session.hf.sample(180, 180) - bump) < 1e-4             # the hand-made bump is replayed, not lost
    assert window.session.hf.sample(60, 60) > 30


def test_dragging_a_river_handle_reroutes_it(window):
    import numpy as np
    from core.world import service as S
    from features.curves.edit import drag_points
    S.create_world("ui", 256, 129); S.apply_terrain_noise(seed=1, amplitude=3, feature_size_m=120)
    S.create_river([[20, 20], [80, 120], [140, 180], [230, 230]], width=6, depth=2)
    window.select_tool("edit_curve"); window.view._rebuild_feature_lines()
    cv = window.view._handles[0]
    assert cv["op"] == len(window.session.op_log) - 1 and cv["key"] == "path"
    j = cv["idx"][len(cv["idx"]) // 2]
    new = drag_points(cv["pts"], j, (cv["pts"][j][0] + 40, cv["pts"][j][1] - 30), cv["closed"])
    before = window.session.hf.data.copy()
    window.on_curve_edited(cv["op"], new)
    op = window.session.op_log[cv["op"]]
    assert op.params["path"] == new and not np.allclose(before, window.session.hf.data)
    assert window.view._handles[0]["pts"].tolist() == new                     # handles follow the edited curve
    window.view.undo()
    assert np.allclose(before, window.session.hf.data) and op.params["path"] != new


def test_history_panel_edits_and_disables_operations(window):
    from core.world import service as S
    S.create_world("ui", 256, 129)
    S.create_mountain(128, 128, 60, 30); S.scatter_vegetation(["bushes"], seed=1)
    window.refresh()
    ed = window.ops
    assert ed.list.count() == 2
    ed.list.setCurrentRow(0)
    assert "height" in ed._fields and "path" not in ed._fields
    ed._fields["height"].setValue(55.0)
    assert ed.changes() == ({"height": 55.0}, None)
    ed._apply()
    assert window.session.op_log[0].params["height"] == 55.0 and window.session.hf.sample(128, 128) > 50
    assert ed.list.currentRow() == 0                                     # selection kept after the replay
    ed.list.setCurrentRow(1); ed.ch_enabled.setChecked(False); ed._apply()
    assert not window.session.op_log[1].enabled and window.session.op_log[1].result is None
    assert ed.list.item(1).text().endswith("(desactivada)")
    window.view.undo()
    assert window.session.op_log[1].enabled and len(window.session.op_log[1].result["_instances"]) > 0
